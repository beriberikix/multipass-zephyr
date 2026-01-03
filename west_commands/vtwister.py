import argparse
import subprocess
import os
import sys
import shutil
from pathlib import Path
from west.commands import WestCommand
from west import log

# Add current directory to sys.path to allow importing multipass_vm
sys.path.append(os.path.dirname(__file__))
from multipass_vm import MultipassVM


class VTwister(WestCommand):
    def __init__(self):
        super().__init__(
            'vtwister',
            'Run Zephyr twister tests in a Multipass VM.',
            'Proxies "west twister" to a Multipass VM for non-POSIX hosts.',
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
        parser.add_argument('--no-sync', action='store_true', help='Skip rsync to local storage, run directly from mount')
        parser.add_argument('--pull-results', action='store_true', help='Pull twister-out directory from VM to host after run')
        parser.add_argument('-O', '--outdir', help='Output directory for twister results')
        parser.add_argument('--keep-warm', action='store_true', help='Do not scale down VM resources after run')

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

        vm_zephyr_base = get_vm_path(zephyr_base)

        # Performance optimization: Sync to local storage (default True)
        if not args.no_sync:
            vm_local_root = "/home/ubuntu/src"
            vm.sync_to_local(vm_workspace, vm_local_root)
            
            # Remap paths to local storage
            def get_local_path(host_path):
                rel = Path(host_path).relative_to(workspace_root)
                return str(Path(vm_local_root) / rel)
            
            vm_zephyr_base = get_local_path(zephyr_base)
            # Re-update the VM workspace root for command execution
            vm_workspace = vm_local_root

        # Execute from workspace root in VM
        env_setup = vm._get_env_setup() # Use central env setup
        
        # Suggested by user: run zephyr-export after mount/sync
        vm.zephyr_export(vm_workspace, vm_zephyr_base)
        
        # Suggested by user: install python packages after mount/sync
        vm.west_packages_pip_install(vm_workspace, vm_zephyr_base)

        # Thread maximization: Get VM CPUS
        vm_cpus, _ = vm.get_current_resources()

        # Build twister command
        twister_cmd = ['west', 'twister']
        
        # If user provided -O / --outdir, we use it
        vm_outdir = "twister-out"
        if args.outdir:
            if os.path.isabs(args.outdir):
                vm_outdir = get_vm_path(args.outdir)
                if not args.no_sync:
                    vm_outdir = get_local_path(args.outdir)
            else:
                vm_outdir = args.outdir
            
            twister_cmd.extend(['-O', vm_outdir])

        # Maximize threads for Twister
        if vm_cpus:
            # Twister uses --jobs
            # Check if user already provided --jobs / -j
            has_jobs = False
            for u_arg in unknown_args:
                if u_arg == '--jobs' or u_arg == '-j' or u_arg.startswith('--jobs=') or u_arg.startswith('-j'):
                    has_jobs = True
                    break
            
            if not has_jobs:
                twister_cmd.extend(['--jobs', str(vm_cpus)])

        twister_cmd.extend(unknown_args)

        log.inf(f"Running twister in VM '{args.vm_name}'...")
        
        # Note:Twister needs a larger environment setup, but _get_env_setup should cover it
        full_command = f"cd {vm_workspace} && export ZEPHYR_BASE={vm_zephyr_base} && {env_setup} && {' '.join(twister_cmd)}"
        
        rc = vm.exec_shell(full_command)
        
        if args.pull_results:
            log.inf(f"Pulling results from {vm_outdir} to host...")
            
            vm_abs_outdir = vm_outdir
            if not os.path.isabs(vm_outdir):
                vm_abs_outdir = os.path.join(vm_workspace, vm_outdir)
            
            if not args.no_sync:
                # Sync back to mount first
                vm_mount_workspace = '/mnt/workspace_vbuild'
                print(f"Syncing results back to mount...")
                # Ensure the target directory exists in mount
                vm.exec_shell(f"mkdir -p {vm_mount_workspace}/{vm_outdir}")
                sync_back_cmd = f"rsync -a --delete {vm_abs_outdir}/ {vm_mount_workspace}/{vm_outdir}/"
                vm.exec_shell(sync_back_cmd)
            
            host_outdir = args.outdir or os.path.join(os.getcwd(), 'twister-out')
            log.inf(f"Results available on host at: {host_outdir}")

        if rc != 0:
            log.die(f"Twister failed with return code {rc}")
        
        log.inf("Twister completed successfully.")
