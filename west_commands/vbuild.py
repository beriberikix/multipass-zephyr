import argparse
import subprocess
import os
import sys
from pathlib import Path
from west.commands import WestCommand
from west import log

# Add current directory to sys.path to allow importing multipass_vm
sys.path.append(os.path.dirname(__file__))
from multipass_vm import MultipassVM


class VBuild(WestCommand):
    def __init__(self):
        super().__init__(
            'vbuild',
            'Build a Zephyr application in a Multipass VM.',
            'Proxies "west build" to a Multipass VM for non-POSIX hosts.',
            accepts_unknown_args=True
        )

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(
            self.name,
            help=self.help,
            description=self.description,
            formatter_class=argparse.RawDescriptionHelpFormatter
        )

        parser.add_argument('--vm-name', default='zephyr-vm', help='Name of the Multipass VM to use')
        parser.add_argument('-s', '--source-dir', help='Source directory to build')
        parser.add_argument('-d', '--build-dir', help='Build directory')
        parser.add_argument('-b', '--board', help='Board to build for')
        parser.add_argument('--pull', '--sync', dest='pull', action='store_true', help='Pull build artifacts from VM to host after build')
        parser.add_argument('-p', '--pristine', action='store_true', help='Remove existing build directory in VM before building')
        parser.add_argument('--no-sync', action='store_true', help='Skip rsync to local storage, build directly from mount')
        parser.add_argument('--keep-warm', action='store_true', help='Do not scale down VM resources after build')

        return parser

    def do_run(self, args, unknown_args):
        vm = MultipassVM(args.vm_name)
        if not vm.is_multipass_installed():
            log.die("Multipass is not installed. Please install it from https://multipass.run/")

        # Dynamic Resource Scaling: Scale UP
        vm.ensure_resources('high')
        
        try:
            self._do_run_internal(vm, args, unknown_args)
        finally:
            if not args.keep_warm:
                vm.ensure_resources('low')

    def _do_run_internal(self, vm, args, unknown_args):
        # Determine paths
        zephyr_base = os.environ.get('ZEPHYR_BASE')
        if not zephyr_base:
            log.die("ZEPHYR_BASE environment variable is not set. Please run 'source zephyr-env.sh' or equivalent.")
        
        zephyr_base = str(Path(zephyr_base).resolve())

        # Find workspace root
        from west.util import west_topdir, WestNotFound
        try:
            workspace_root = west_topdir(os.getcwd())
        except WestNotFound:
            workspace_root = os.path.dirname(zephyr_base) # fallback
        
        workspace_root = str(Path(workspace_root).resolve())

        # Pass target resources if they were set by ensure_resources
        target_cpus = getattr(vm, 'target_cpus', None)
        target_mem = getattr(vm, 'target_memory', None)
        vm.ensure_vm(zephyr_base, cpus=target_cpus, memory=target_mem)

        # Determine source and build dirs
        source_dir = args.source_dir
        remainder = []
        source_dir_found = False
        for arg in unknown_args:
            if not source_dir_found and not arg.startswith('-') and os.path.isdir(arg):
                source_dir = arg
                source_dir_found = True
            else:
                remainder.append(arg)
        
        if not source_dir:
            source_dir = os.getcwd()

        source_dir = str(Path(source_dir).resolve())
        
        # Build dir resolution
        build_dir = args.build_dir or os.path.join(source_dir, 'build')
        build_dir = str(Path(build_dir).resolve())

        # VM Mount points
        vm_workspace = '/mnt/workspace_vbuild'
        
        # Mounting the entire workspace
        vm.mount(workspace_root, vm_workspace)
        
        # Path remapping logic
        def get_vm_path(host_path):
            try:
                rel = Path(host_path).relative_to(workspace_root)
                return str(Path(vm_workspace) / rel)
            except ValueError:
                import hashlib
                h = hashlib.md5(host_path.encode()).hexdigest()[:8]
                vm_path = f"/mnt/ext_{h}"
                vm.mount(host_path, vm_path)
                return vm_path

        vm_source_dir = get_vm_path(source_dir)
        vm_zephyr_base = get_vm_path(zephyr_base)

        # Performance optimization: Sync to local storage
        if not args.no_sync:
            vm_local_root = "/home/ubuntu/src"
            vm.sync_to_local(vm_workspace, vm_local_root)
            
            # Remap paths to local storage
            def get_local_path(host_path):
                rel = Path(host_path).relative_to(workspace_root)
                return str(Path(vm_local_root) / rel)
            
            vm_source_dir = get_local_path(source_dir)
            vm_zephyr_base = get_local_path(zephyr_base)
            # Re-update the VM workspace root for command execution
            vm_workspace = vm_local_root

        # Build dir resolution
        # Default to an internal VM path to avoid mount permission issues
        import hashlib
        h = hashlib.md5(source_dir.encode()).hexdigest()[:8]
        vm_build_dir = f"/home/ubuntu/builds/{h}"
        
        # If user provided -d, we use it relative to workspace or as absolute
        if args.build_dir:
            vm_build_dir = get_vm_path(os.path.abspath(args.build_dir))
        
        # Ensure internal build dir exists
        if args.pristine:
            vm.delete_dir(vm_build_dir)
        vm.exec_shell(f"mkdir -p {vm_build_dir}")

        # Suggested by user: run zephyr-export after mount
        vm.zephyr_export(vm_workspace, vm_zephyr_base)
        
        # Suggested by user: install python packages after mount
        vm.west_packages_pip_install(vm_workspace, vm_zephyr_base)

        # Thread maximization: Get VM CPUS
        vm_cpus, _ = vm.get_current_resources()

        # Build command
        west_cmd = ['west', 'build']
        west_cmd.extend(['-s', vm_source_dir])
        west_cmd.extend(['-d', vm_build_dir])
        if args.board:
            west_cmd.extend(['-b', args.board])
        
        # Maximize threads for Ninja
        # west build passes options to the underlying build tool (ninja) via -o
        if vm_cpus:
            west_cmd.extend([f'-o=-j{vm_cpus}'])

        west_cmd.extend(remainder)

        log.inf(f"Running build in VM '{args.vm_name}'...")
        
        # Execute from workspace root in VM
        # Enable ccache natively as per plan
        env_setup = f"export ZEPHYR_BASE={vm_zephyr_base} && export CCACHE=1"
        full_command = f"cd {vm_workspace} && {env_setup} && {' '.join(west_cmd)}"
        
        rc = vm.exec_shell(full_command)
        if rc != 0:
            log.die(f"Build failed with return code {rc}")
        
        log.inf("Build completed successfully.")

        if args.pull:
            log.inf("Pulling artifacts to host...")
            # Files to pull
            artifacts = [
                'zephyr/zephyr.elf',
                'zephyr/zephyr.exe',
                'zephyr/zephyr.bin',
                'zephyr/zephyr.map',
            ]
            for art in artifacts:
                vm_art_path = f"{vm_build_dir}/{art}"
                host_art_path = os.path.join(build_dir, art)
                
                # Check if file exists in VM before pulling
                check_cmd = f"multipass exec {vm.vm_name} -- test -f {vm_art_path}"
                res = subprocess.run(check_cmd, shell=True, capture_output=True)
                if res.returncode == 0:
                    vm.pull_file(vm_art_path, host_art_path)
