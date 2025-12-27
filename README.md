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
# On Apple Silicon:
west vbuild -b native_sim/native/64 zephyr/samples/hello_world

# On Intel:
west vbuild -b native_sim zephyr/samples/hello_world
```

The first run will automatically:
1. Create a `zephyr-vm` Multipass instance.
2. Install the Zephyr SDK and build dependencies.
3. Mount your workspace root into the VM.

### Running

Run the simulated application:

```bash
west vrun zephyr/samples/hello_world
```

You'll see the Zephyr boot banner and application output directly in your host terminal.

## How it Works

- **Storage**: Builds are performed in the VM's internal filesystem (under `/home/ubuntu/builds/`) to ensure maximum speed and avoid permission issues common with network mounts.
- **Hashing**: Source directory paths are hashed to generate unique build directories in the VM, allowing you to work on multiple projects simultaneously.
- **Environment**: Automatically manages `ZEPHYR_BASE` and toolchain paths inside the VM.

## License

Apache-2.0
