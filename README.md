# Multipass Zephyr: West build & run proxy

`multipass-zephyr` is a West extension that enables building and running Zephyr `native_sim` targets on non-POSIX hosts (macOS, Windows) by proxying operations to a Canonical Multipass VM.

## Why?

Zephyr's `native_sim` is a powerful tool for developing and testing applications on your host machine without hardware. However, it is natively designed for Linux. Users on macOS and Windows often struggle with toolchain setup or lack of POSIX features.

This tool provides a transparent bridge:
- **`west vbuild`**: Compiles your code inside a streamlined Linux VM.
- **`west vrun`**: Executes the result in the VM and streams the output to your terminal.

## Prerequisites

- [Multipass](https://multipass.run/) installed on your host.
- [West](https://docs.zephyrproject.org/latest/develop/west/index.html) installed and initialized.
- Python 3.10+.

## Installation

Add this project to your Zephyr workspace manifest (`west.yml`):

```yaml
manifest:
  projects:
    - name: multipass-zephyr
      url: https://github.com/beriberikix/multipass-zephyr
      revision: main
      west-commands: west-commands.yml
```

Run `west update` to pull the project.

## Usage

### Building

Build a sample application:

```bash
# Basic build
west vbuild -b native_sim/native/64 zephyr/samples/hello_world

# Build and sync artifacts back to the host's build/ directory
west vbuild --sync -b native_sim/native/64 zephyr/samples/hello_world

# Pristine (clean) build
west vbuild -p -b native_sim/native/64 zephyr/samples/hello_world
```

The first run will automatically:
1. Create a `zephyr-vm` Multipass instance.
2. **Auto-detect** the required Zephyr SDK version from your workspace's `SDK_VERSION` file and install it.
3. Install all necessary build dependencies.
4. Mount your workspace root into the VM.

### Running

Run the simulated application:

```bash
west vrun zephyr/samples/hello_world
```

### Cleaning

Manage VM storage:

```bash
# Clean the build for the current project
west vclean

# Clean ALL proxy builds in the VM
west vclean --all
```

## How it Works

- **Storage**: Builds are performed in the VM's internal filesystem (under `/home/ubuntu/builds/`) to ensure maximum speed and avoid permission issues common with network mounts.
- **Hashing**: Source directory paths are hashed to generate unique build directories in the VM.
- **SDK Auto-Detection**: Automatically reads the `SDK_VERSION` file from your Zephyr base directory to ensure the toolchain matches your project's requirements.
- **Artifact Syncing**: Pulls key binaries (`zephyr.elf`, `zephyr.exe`, `zephyr.map`) back to your host when using the `--sync` flag.

## License

Apache-2.0
