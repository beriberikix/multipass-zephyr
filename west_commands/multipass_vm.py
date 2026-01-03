import subprocess
import os
import json
import sys
import shutil
import platform
import multiprocessing
from pathlib import Path
from west import log

class MultipassVM:
    def __init__(self, vm_name='zephyr-vm'):
        self.vm_name = vm_name
        self.ubuntu_version = '24.04'
        self.default_cpus = 2
        self.default_memory = '4G'
        self.disk = '20G'

    def _run_cmd(self, cmd, capture_output=True, check=True):
        try:
            result = subprocess.run(cmd, capture_output=capture_output, text=True, check=check)
            return result
        except subprocess.CalledProcessError as e:
            if check:
                print(f"Error running command: {' '.join(cmd)}")
                print(f"Stdout: {e.stdout}")
                print(f"Stderr: {e.stderr}")
                raise
            return e

    def get_status(self):
        result = self._run_cmd(['multipass', 'list', '--format', 'json'])
        vms = json.loads(result.stdout).get('list', [])
        for vm in vms:
            if vm['name'] == self.vm_name:
                return vm['state'].lower()
        return 'not-found'

    def _get_env_setup(self):
        # Explicitly define paths and variables to avoid bashrc sourcing issues
        # Prioritize venv if it exists
        paths = "export PATH=/home/ubuntu/.venv/bin:$PATH:$HOME/.local/bin"
        envs = "export ZEPHYR_TOOLCHAIN_VARIANT=zephyr && export ZEPHYR_SDK_INSTALL_DIR=/home/ubuntu/zephyr-sdk && export PIP_BREAK_SYSTEM_PACKAGES=1"
        return f"{paths} && {envs}"

    def _is_setup(self):
        print("  [VM] Checking dependencies...")
        env = self._get_env_setup()
        
        # Check tools
        for cmd in ["west", "cmake", "ninja", "brctl", "uv"]:
            check_cmd = f"multipass exec {self.vm_name} -- bash -c '{env} && which {cmd}'"
            res = subprocess.run(check_cmd, shell=True, capture_output=True)
            if res.returncode != 0:
                print(f"  [VM] Component '{cmd}' not found. Setup required.")
                return False
        
        # Check venv
        print("  [VM] Checking for virtual environment...")
        venv_check = f"multipass exec {self.vm_name} -- bash -c 'test -d /home/ubuntu/.venv'"
        res = subprocess.run(venv_check, shell=True, capture_output=True)
        if res.returncode != 0:
            print("  [VM] Virtual environment not found. Setup required.")
            return False
        
        # Check SDK
        print("  [VM] Checking for Zephyr SDK...")
        sdk_check = f"multipass exec {self.vm_name} -- bash -c 'ls -d /home/ubuntu/zephyr-sdk'"
        res = subprocess.run(sdk_check, shell=True, capture_output=True)
        if res.returncode != 0:
            print("  [VM] Zephyr SDK not found at /home/ubuntu/zephyr-sdk. Setup required.")
            return False

        print("  [VM] Dependencies and SDK verified.")
        return True

    def ensure_vm(self, zephyr_base_path=None, cpus=None, memory=None):
        status = self.get_status()
        target_cpus = cpus or self.default_cpus
        target_mem = memory or self.default_memory

        if status == 'not-found':
            print(f"Creating Multipass VM '{self.vm_name}'...")
            self._run_cmd(['multipass', 'launch', '24.04', '--name', self.vm_name, 
                           '--cpus', str(target_cpus), '--memory', target_mem, '--disk', self.disk])
            self._setup_vm(zephyr_base_path)
        elif status == 'stopped':
            print(f"Starting Multipass VM '{self.vm_name}'...")
            self._run_cmd(['multipass', 'start', self.vm_name])
            if not self._is_setup():
                self._setup_vm(zephyr_base_path)
        elif status == 'running':
            if not self._is_setup():
                self._setup_vm(zephyr_base_path)
            else:
                print("VM is ready.")

    def get_host_resources(self):
        """Detect host resources safely for cross-platform support."""
        # CPUs
        total_cpus = multiprocessing.cpu_count()
        safe_cpus = max(2, total_cpus - 2)
        
        # Memory (Bytes)
        total_mem_bytes = 0
        sys_pf = platform.system()
        try:
            if sys_pf == "Darwin":
                res = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
                total_mem_bytes = int(res.stdout.strip())
            elif sys_pf == "Windows":
                res = subprocess.run(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"], capture_output=True, text=True)
                lines = res.stdout.strip().splitlines()
                if len(lines) > 1:
                    total_mem_bytes = int(lines[1].strip())
            elif sys_pf == "Linux":
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if "MemTotal" in line:
                            total_mem_bytes = int(line.split()[1]) * 1024
                            break
        except Exception as e:
            print(f"Warning: Failed to detect host memory: {e}")
            total_mem_bytes = 8 * 1024 * 1024 * 1024 # Fallback 8GB
        
        # Calculation: 75% of total, capped at Total - 4GB
        limit_mem_bytes = min(int(total_mem_bytes * 0.75), total_mem_bytes - (4 * 1024 * 1024 * 1024))
        safe_memory_gb = max(4, int(limit_mem_bytes / (1024 * 1024 * 1024)))
        
        return safe_cpus, f"{safe_memory_gb}G"

    def get_current_resources(self):
        """Get current VM CPU and Memory settings."""
        try:
            cpus_res = self._run_cmd(['multipass', 'get', f'local.{self.vm_name}.cpus'])
            mem_res = self._run_cmd(['multipass', 'get', f'local.{self.vm_name}.memory'])
            return int(cpus_res.stdout.strip()), mem_res.stdout.strip()
        except:
            return None, None

    def ensure_resources(self, profile='low'):
        """Apply high or low resource profile to the VM."""
        status = self.get_status()
        if profile == 'high':
            target_cpus, target_mem = self.get_host_resources()
        else:
            target_cpus, target_mem = self.default_cpus, self.default_memory

        if status == 'not-found':
            # Store targets in instance for ensure_vm to find if it follows
            self.target_cpus, self.target_memory = target_cpus, target_mem
            return

        current_cpus, current_mem = self.get_current_resources()
        
        # Only restart if there's a significant change
        if current_cpus != target_cpus or current_mem != target_mem:
            print(f"Applying '{profile}' resource profile ({target_cpus} CPUs, {target_mem} RAM)...")
            status = self.get_status()
            if status == 'running':
                print("Stopping VM to reconfigure...")
                self._run_cmd(['multipass', 'stop', self.vm_name])
            
            self._run_cmd(['multipass', 'set', f'local.{self.vm_name}.cpus={target_cpus}'])
            self._run_cmd(['multipass', 'set', f'local.{self.vm_name}.memory={target_mem}'])
            
            print("Starting VM with new configuration...")
            self._run_cmd(['multipass', 'start', self.vm_name])
        else:
            print(f"Already using '{profile}' resource profile.")

    def _setup_vm(self, zephyr_base_path=None):
        print("Setting up VM dependencies and Zephyr SDK...")

        # BUG: On Ubuntu 24.04, Multipass mount helper can fail after resource restarts.
        # Installing multipass-sshfs manually is the official workaround.
        print("  [VM] Installing mount helpers (Multipass bug workaround)...")
        self.exec_shell("sudo apt update && sudo apt install -y sshfs snapd && sudo snap install multipass-sshfs", check=False)

        # Detect SDK version from host workspace if available
        sdk_version = "0.17.0"  # Default fallback
        if zephyr_base_path:
            sdk_version_file = os.path.join(zephyr_base_path, "SDK_VERSION")
            if os.path.exists(sdk_version_file):
                try:
                    with open(sdk_version_file, 'r') as f:
                        sdk_version = f.read().strip()
                    print(f"Detected Zephyr SDK version: {sdk_version}")
                except Exception as e:
                    print(f"Warning: Could not read SDK_VERSION file at {sdk_version_file}: {e}")
                    print(f"Using default SDK version: {sdk_version}")
        
        # 1. Install packages from user's verified list
        packages = [
            "git", "cmake", "ninja-build", "gperf", "ccache", "device-tree-compiler",
            "wget", "file", "libmagic1", "xz-utils", "python3-dev", "python3-pip",
            "python3-setuptools", "python3-wheel", "build-essential", "libsdl2-dev",
            "net-tools", "iproute2", "bridge-utils"
        ]
        install_cmd = f"sudo apt-get update && sudo apt-get install -y --no-install-recommends {' '.join(packages)}"
        self.exec_shell(install_cmd)

        # 2. Install Zephyr SDK (Architecture-aware)
        sdk_setup = f"""
        set -e
        ARCH=$(uname -m)
        SDK_VERSION="{sdk_version}"
        if [ "$ARCH" = "x86_64" ]; then
            SDK_URL="https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v${{SDK_VERSION}}/zephyr-sdk-${{SDK_VERSION}}_linux-x86_64_minimal.tar.xz"
        elif [ "$ARCH" = "aarch64" ]; then
            SDK_URL="https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v${{SDK_VERSION}}/zephyr-sdk-${{SDK_VERSION}}_linux-aarch64_minimal.tar.xz"
        else
            echo "Unsupported architecture: $ARCH"
            exit 1
        fi
        
        if [ ! -d /home/ubuntu/zephyr-sdk ]; then
            echo "Downloading and installing Zephyr SDK v${{SDK_VERSION}} for ${{ARCH}}..."
            wget -q --show-progress ${{SDK_URL}} -O /tmp/sdk.tar.xz
            cd /home/ubuntu
            tar xf /tmp/sdk.tar.xz
            # Correcting the directory name based on extraction results
            if [ -d zephyr-sdk-${{SDK_VERSION}} ]; then
                mv zephyr-sdk-${{SDK_VERSION}} /home/ubuntu/zephyr-sdk
            else
                # Fallback in case the name is different
                mv zephyr-sdk-${{SDK_VERSION}}_linux-${{ARCH}}_minimal /home/ubuntu/zephyr-sdk
            fi
            rm /tmp/sdk.tar.xz
            /home/ubuntu/zephyr-sdk/setup.sh -c
            echo "SDK installation complete."
        else
            echo "SDK already exists."
        fi
        """
        print("Starting Zephyr SDK installation...")
        self.exec_shell(sdk_setup)

        # Verify SDK exists
        res = self._run_cmd(['multipass', 'exec', self.vm_name, '--', 'test', '-d', '/home/ubuntu/zephyr-sdk'], check=False)
        if res.returncode != 0:
            print("FATAL: Zephyr SDK installation failed - directory not found.")
            raise RuntimeError("Zephyr SDK installation failed")

        # 3. Install uv and setup venv
        print("Installing uv and setting up virtual environment...")
        self.exec_shell("curl -LsSf https://astral.sh/uv/install.sh | sh")
        self.exec_shell("export PATH=$PATH:$HOME/.local/bin && uv venv /home/ubuntu/.venv")
        
        # 4. Install west into venv
        self.exec_shell("uv pip install west")

        # 4. Set persistent environment variables (best effort for interactive sessions)
        env_cmds = [
            "echo 'export ZEPHYR_TOOLCHAIN_VARIANT=zephyr' >> /home/ubuntu/.bashrc",
            "echo 'export ZEPHYR_SDK_INSTALL_DIR=/home/ubuntu/zephyr-sdk' >> /home/ubuntu/.bashrc",
            "echo 'export PATH=$PATH:$HOME/.local/bin' >> /home/ubuntu/.bashrc",
            "ccache --max-size=5G",
            "ccache --set-config=cache_dir=/home/ubuntu/.ccache"
        ]
        for cmd in env_cmds:
            self._run_cmd(['multipass', 'exec', self.vm_name, '--', 'bash', '-c', cmd])

    def zephyr_export(self, vm_workspace, vm_zephyr_base):
        print("Exporting Zephyr to CMake package registry in VM...")
        self.exec_shell(f"export ZEPHYR_BASE={vm_zephyr_base} && cd {vm_workspace} && west zephyr-export")

    def west_packages_pip_install(self, vm_workspace, vm_zephyr_base):
        print("Installing Python dependencies (using uv)...")
        # Use uv for all requirement files found in zephyr
        # This is significantly faster than west packages pip
        req_files = [
            f"{vm_zephyr_base}/scripts/requirements.txt",
            f"{vm_zephyr_base}/scripts/requirements-base.txt",
            f"{vm_zephyr_base}/scripts/requirements-build-test.txt",
            f"{vm_zephyr_base}/scripts/requirements-run-test.txt",
            f"{vm_zephyr_base}/scripts/requirements-extras.txt",
            f"{vm_zephyr_base}/scripts/requirements-compliance.txt"
        ]
        
        # Build one big install command for uv
        req_args = " ".join([f"-r {f}" for f in req_files])
        self.exec_shell(f"uv pip install {req_args} pyelftools")

    def mount(self, host_path, vm_path):
        print(f"Mounting {host_path} to {vm_path}...")
        # Ensure vm_path exists (as root/sudo)
        self._run_cmd(['multipass', 'exec', self.vm_name, '--', 'sudo', 'mkdir', '-p', vm_path])
        
        # Check if already mounted
        result = self._run_cmd(['multipass', 'info', self.vm_name, '--format', 'json'])
        info = json.loads(result.stdout).get('info', {}).get(self.vm_name, {})
        mounts = info.get('mounts', {})
        if vm_path in mounts:
            if mounts[vm_path]['source_path'] == str(Path(host_path).expanduser().resolve()):
                return
            else:
                self._run_cmd(['multipass', 'unmount', f"{self.vm_name}:{vm_path}"])

        self._run_cmd(['multipass', 'mount', host_path, f"{self.vm_name}:{vm_path}"])

    def exec_shell(self, cmd, stream=True, check=True):
        env = self._get_env_setup()
        full_cmd = f"{env} && {cmd}"
        multipass_cmd = ['multipass', 'exec', self.vm_name, '--', 'bash', '-c', full_cmd]
        if stream:
            return subprocess.run(multipass_cmd).returncode
        else:
            result = self._run_cmd(multipass_cmd, check=check)
            return result.stdout if check else result

    def pull_file(self, vm_path, host_path):
        print(f"Transferring {vm_path} from VM to {host_path}...")
        # Ensure host directory exists
        host_dir = os.path.dirname(os.path.abspath(host_path))
        os.makedirs(host_dir, exist_ok=True)
        
        # Multipass transfer syntax: <vm_name>:<vm_path> <host_path>
        self._run_cmd(['multipass', 'transfer', f"{self.vm_name}:{vm_path}", host_path])

    def delete_dir(self, vm_path):
        print(f"Deleting directory {vm_path} in VM...")
        self.exec_shell(f"rm -rf {vm_path}")

    def sync_to_local(self, vm_mount_path, vm_local_path):
        """Rsync from mount to local storage inside VM."""
        print(f"Syncing {vm_mount_path} to {vm_local_path}...")
        # Ensure target directory exists
        self.exec_shell(f"mkdir -p {vm_local_path}")
        # Anchored rsync with common ignores (leading slash means root-relative)
        sync_cmd = f'''
            rsync -a --delete \
                --exclude='/.git/' \
                --exclude='/build/' \
                --exclude='/builds/' \
                --exclude='/twister-out*/' \
                --exclude='__pycache__/' \
                --exclude='*.pyc' \
                --exclude='*.o' \
                --exclude='/.cache/' \
                {vm_mount_path}/ {vm_local_path}/
        '''
        self.exec_shell(sync_cmd)
        
    def setup_native_sim_network(self):
        """Set up TAP networking for native_sim if not already configured."""
        print("Setting up TAP network interface for native_sim...")
        
        # Check if zeth interface already exists
        check_cmd = "ip link show zeth 2>/dev/null"
        # We use stream=False and check=False to capture the return code without raising an error
        result = self.exec_shell(check_cmd, stream=False, check=False)
        
        # If result is a CompletedProcess object (from _run_cmd via exec_shell(stream=False))
        if hasattr(result, 'returncode') and result.returncode == 0:
            print("TAP interface 'zeth' already configured.")
            return
        # Handle string return (stdout) if check was True or returncode wasn't available
        elif isinstance(result, str) and "zeth" in result:
             print("TAP interface 'zeth' already configured.")
             return

        # Run net-setup.sh from tools/net-tools
        # Note: We assume the workspace is already synced to /home/ubuntu/src
        net_tools_path = "/home/ubuntu/src/tools/net-tools"
        setup_cmd = f"cd {net_tools_path} && sudo ./net-setup.sh up"
        
        print(f"Running {setup_cmd}...")
        rc = self.exec_shell(setup_cmd)
        if rc != 0:
            log.wrn("Network setup failed. Networking samples may not work correctly.")
        else:
            print("TAP interface configured successfully.")

    def is_multipass_installed(self):
        return shutil.which('multipass') is not None
