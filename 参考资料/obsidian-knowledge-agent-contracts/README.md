# Obsidian Knowledge Agent Contracts — Foundation Schemas

This package contains the first contract layer for the Obsidian plugin ↔ local Agent protocol.

## Included groups

- `schemas/common/`: identity, timestamps, source, errors and the base message envelope.
- `schemas/protocol/`: capability declaration and handshake request/response.
- `schemas/operations/`: operation base, supporting types, six initial concrete operations and `OperationPlan`.

All schemas use JSON Schema Draft 2020-12 and canonical IDs under:

```text
https://schemas.obsidian-knowledge-agent.dev/contracts/v1/
```

## Validate

```bash
python scripts/validate.py
```

Dependencies:

```bash
pip install "jsonschema[format]>=4.21" referencing
```

## Important implementation rules

1. A concrete message extends `common/envelope.schema.json` and closes the object with `unevaluatedProperties: false`.
2. A concrete operation extends `operations/operation-base.schema.json` and closes the object with `unevaluatedProperties: false`.
3. The executor must still perform semantic checks that JSON Schema cannot express, such as `endOffset >= startOffset`, dependency-cycle detection, unique operation IDs, operation ordering, path traversal rejection, expiration comparison and plan-hash verification.
4. Unknown operation types must be rejected until their schema and executor capability are both registered.
