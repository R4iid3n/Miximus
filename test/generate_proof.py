#!/usr/bin/env python3
"""Helper script to generate zkSNARK proof via C++ library. Called from Node.js."""
import ctypes
import json
import sys
import os

def main():
    if len(sys.argv) != 2:
        sys.stderr.write("Usage: generate_proof.py <args_json_file>\n")
        sys.exit(1)

    args_file = sys.argv[1]
    # Output file is the input file with .result.json extension
    result_file = args_file.replace(".json", ".result.json")

    with open(args_file, "r") as f:
        args = json.load(f)

    lib_path = "/mnt/c/AML mixer/ethsnarks-miximus/.build/libmiximus.so"
    pk_path = "/mnt/c/AML mixer/ethsnarks-miximus/.keys/miximus.pk.raw"

    if not os.path.exists(lib_path):
        with open(result_file, "w") as f:
            json.dump({"error": f"Library not found: {lib_path}"}, f)
        return
    if not os.path.exists(pk_path):
        with open(result_file, "w") as f:
            json.dump({"error": f"Proving key not found: {pk_path}"}, f)
        return

    # Suppress ALL C/C++ library output (libsnark prints to both stdout and stderr)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    os.dup2(devnull_fd, 1)
    os.dup2(devnull_fd, 2)
    os.close(devnull_fd)

    try:
        lib = ctypes.cdll.LoadLibrary(lib_path)

        # Compute nullifier - miximus_nullifier expects decimal strings (FieldT constructor)
        lib.miximus_nullifier.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        lib.miximus_nullifier.restype = ctypes.c_char_p
        secret_dec = str(int(args["secret"], 16) if isinstance(args["secret"], str) and args["secret"].startswith("0x") else int(args["secret"]))
        address_dec = str(int(args["address"]))
        nullifier = int(lib.miximus_nullifier(
            ctypes.c_char_p(secret_dec.encode("ascii")),
            ctypes.c_char_p(address_dec.encode("ascii"))
        ))

        # Generate proof
        lib.miximus_prove_json.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        lib.miximus_prove_json.restype = ctypes.c_char_p

        prove_args = {
            "root": args["root"],
            "exthash": args["exthash"],
            "secret": args["secret"],
            "address": args["address"],
            "path": args["path"]
        }

        result = lib.miximus_prove_json(
            ctypes.c_char_p(pk_path.encode("ascii")),
            ctypes.c_char_p(json.dumps(prove_args).encode("ascii"))
        )
    finally:
        # Restore stdout/stderr
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)

    if result is None:
        with open(result_file, "w") as f:
            json.dump({"error": "C++ prover returned null"}, f)
        return

    proof = json.loads(result)
    proof["nullifier"] = str(nullifier)

    # Write result to file (avoids stdout pollution from C++ library)
    with open(result_file, "w") as f:
        json.dump(proof, f)


if __name__ == "__main__":
    main()
