"""Regenerate runtime contracts and make gRPC's experimental import explicit."""

from pathlib import Path

from grpc_tools import protoc


def main() -> None:
    """Generate Python, type stubs and gRPC services using locked tool versions."""
    root = Path(__file__).resolve().parents[1] / "packages" / "mas-proto"
    source = root / "proto"
    output = root / "src"
    result = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{source}",
            f"--python_out={output}",
            f"--pyi_out={output}",
            f"--grpc_python_out={output}",
            str(source / "mas_proto" / "runtime" / "v1" / "runtime.proto"),
        ]
    )
    if result != 0:
        raise RuntimeError(f"Protocol generation failed with exit code {result}")
    bindings = output / "mas_proto" / "runtime" / "v1" / "runtime_pb2_grpc.py"
    code = bindings.read_text()
    # grpcio-tools emits experimental calls without importing their submodule.
    if "grpc.experimental." in code and "import grpc.experimental\n" not in code:
        bindings.write_text(
            code.replace("import grpc\n", "import grpc\nimport grpc.experimental\n", 1)
        )


if __name__ == "__main__":
    main()
