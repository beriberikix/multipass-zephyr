import argparse
import os
import sys
from pathlib import Path
from west.commands import WestCommand
from west import log

# Add current directory to sys.path to allow importing multipass_vm
sys.path.append(os.path.dirname(__file__))
from multipass_vm import MultipassVM


class VClean(WestCommand):
    def __init__(self):
        super().__init__(
            'vclean',
            'Clean build directories in a Multipass VM.',
            'Removes build artifacts from the VM to reclaim disk space.',
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
        parser.add_argument('--all', action='store_true', help='Remove ALL build directories in the VM')
        parser.add_argument('source_dir', nargs='?', help='Source directory whose build should be cleaned')

        return parser

    def do_run(self, args, unknown_args):
        vm = MultipassVM(args.vm_name)
        if not vm.is_multipass_installed():
            log.die("Multipass is not installed.")

        status = vm.get_status()
        if status == 'not-found':
            log.inf(f"VM '{args.vm_name}' does not exist. Nothing to clean.")
            return

        if args.all:
            log.inf("Cleaning ALL builds in VM...")
            vm.delete_dir("/home/ubuntu/builds/*")
            log.inf("Done.")
            return

        # Targeted clean
        source_dir = args.source_dir
        if not source_dir:
            source_dir = os.getcwd()
        
        source_dir = str(Path(source_dir).resolve())
        
        import hashlib
        h = hashlib.md5(source_dir.encode()).hexdigest()[:8]
        vm_build_dir = f"/home/ubuntu/builds/{h}"
        
        log.inf(f"Cleaning build for {source_dir} (VM path: {vm_build_dir})...")
        vm.delete_dir(vm_build_dir)
        log.inf("Done.")
