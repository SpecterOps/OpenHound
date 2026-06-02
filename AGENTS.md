# Agent Guidelines

## Branch Naming

All branches **must** follow one of these formats or the CI pipeline will reject the push:

```
fix/<description>
patch/<description>
feature/<description>
minor/<description>
major/<description>
```

**Examples:**
- `feature/BED-1234-add-support-bundle-upload`
- `fix/BED-5678-correct-management-endpoint-path`
- `patch/bump-pydantic-version`

Use lowercase `<description>` with hyphens. Include the ticket ID when one exists.
