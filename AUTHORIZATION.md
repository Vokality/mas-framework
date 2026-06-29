# Authorization

MAS is deny-by-default.

The server uses `AuthorizationModule` from `mas-gateway`. Host applications grant access programmatically after server startup:

```python
await server.authz.set_permissions("agent_a", allowed_targets=["agent_b"])
await server.authz.set_permissions("agent_a", blocked_targets=["agent_c"])
```

Each call replaces the provided list in full. Pass `None` for a list to leave it
unchanged. Blocked targets take precedence over allowed targets.

Rules are stored in Redis sets:
- `agent:{agent_id}:allowed_targets`
- `agent:{agent_id}:blocked_targets`

When RBAC is enabled in `GatewaySettings.features.rbac`, role assignments and role permission patterns can also grant access. ACL checks remain the simple default.
