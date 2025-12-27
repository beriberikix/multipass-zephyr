import argparse
import os
from pathlib import Path
import sys
from west.commands import WestCommand
from west import log

# Add current directory to sys.path to allow importing multipass_vm
sys.path.append(os.path.dirname(__file__))
from multipass_vm import MultipassVM


class VRun(WestCommand):
    def __init__(self):
        super().__init__(
            'vrun',
            'Run a Zephyr application in a Multipass VM.',
            'Proxies execution of native_sim targets to a Multipass VM.',
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
        parser.add_argument('-d', '--build-dir', help='Build directory containing zephyr.elf')
        parser.add_argument('source_dir_pos', nargs='?', help='Source directory used for build hashing')

        return parser

    def do_run(self, args, unknown_args):
        vm = MultipassVM(args.vm_name)
        if not vm.is_multipass_installed():
            log.die("Multipass is not installed.")

        vm.ensure_vm()

        # Determine workspace root
        zephyr_base = os.environ.get('ZEPHYR_BASE')
        if not zephyr_base:
            log.die("ZEPHYR_BASE not set.")
        
        zephyr_base = str(Path(zephyr_base).resolve())
        from west.util import west_topdir, WestNotFound
        try:
            workspace_root = west_topdir(os.getcwd())
        except WestNotFound:
            workspace_root = os.path.dirname(zephyr_base)
        workspace_root = str(Path(workspace_root).resolve())

        # Determine source dir (needed for hashing if build_dir not provided)
        source_dir = args.source_dir_pos
        remainder = []
        source_dir_found = False
        for arg in unknown_args:
            if not source_dir and not source_dir_found and not arg.startswith('-') and os.path.isdir(arg):
                source_dir = arg
                source_dir_found = True
            else:
                remainder.append(arg)

        if not source_dir:
            source_dir = os.getcwd()
        
        source_dir = str(Path(source_dir).resolve())

        # Build dir resolution
        if args.build_dir:
            # Use provided build dir
            host_build_dir = str(Path(args.build_dir).resolve())
            try:
                rel = Path(host_build_dir).relative_to(workspace_root)
                vm_build_dir = str(Path('/mnt/workspace') / rel)
            except ValueError:
                import hashlib
                h = hashlib.md5(host_build_dir.encode()).hexdigest()[:8]
                vm_build_dir = f"/mnt/ext_{h}"
                vm.mount(host_build_dir, vm_build_dir)
        else:
            # Use hashed internal path (same as vbuild)
            import hashlib
            h = hashlib.md5(source_dir.encode()).hexdigest()[:8]
            vm_build_dir = f"/home/ubuntu/builds/{h}"
            
        # Execute
        # Check for zephyr.exe first (modern native_sim), then zephyr.elf
        find_exe = f"if [ -f {vm_build_dir}/zephyr/zephyr.exe ]; then echo {vm_build_dir}/zephyr/zephyr.exe; else echo {vm_build_dir}/zephyr/zephyr.elf; fi"
        exe = vm._run_cmd(['multipass', 'exec', vm.vm_name, '--', 'bash', '-c', find_exe], check=False).stdout.strip()
        
        if not exe:
             log.die("Could not find zephyr.exe or zephyr.elf in VM.")

        log.inf(f"Running {exe} in VM '{args.vm_name}'...")
        
        full_command = f"chmod +x {exe} && {exe} {' '.join(remainder)}"
        rc = vm.exec_shell(full_command)
        sys.exit(rc)
