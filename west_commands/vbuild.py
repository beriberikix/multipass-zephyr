import argparse
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

        return parser

    def do_run(self, args, unknown_args):
        vm = MultipassVM(args.vm_name)
        if not vm.is_multipass_installed():
            log.die("Multipass is not installed. Please install it from https://multipass.run/")

        vm.ensure_vm()

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
        vm_workspace = '/mnt/workspace'
        
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

        # Build dir resolution
        # Default to an internal VM path to avoid mount permission issues
        import hashlib
        h = hashlib.md5(source_dir.encode()).hexdigest()[:8]
        vm_build_dir = f"/home/ubuntu/builds/{h}"
        
        # If user provided -d, we use it relative to workspace or as absolute
        if args.build_dir:
            vm_build_dir = get_vm_path(os.path.abspath(args.build_dir))
        
        # Ensure internal build dir exists
        vm.exec_shell(f"mkdir -p {vm_build_dir}")

        # Suggested by user: run zephyr-export after mount
        vm.zephyr_export(vm_workspace, vm_zephyr_base)
        
        # Suggested by user: install python packages after mount
        vm.west_packages_pip_install(vm_workspace, vm_zephyr_base)

        # Build command
        west_cmd = ['west', 'build']
        west_cmd.extend(['-s', vm_source_dir])
        west_cmd.extend(['-d', vm_build_dir])
        if args.board:
            west_cmd.extend(['-b', args.board])
        
        west_cmd.extend(remainder)

        log.inf(f"Running build in VM '{args.vm_name}'...")
        
        # Execute from workspace root in VM
        env_setup = f"export ZEPHYR_BASE={vm_zephyr_base}"
        full_command = f"cd {vm_workspace} && {env_setup} && {' '.join(west_cmd)}"
        
        rc = vm.exec_shell(full_command)
        if rc != 0:
            log.die(f"Build failed with return code {rc}")
        
        log.inf("Build completed successfully.")
