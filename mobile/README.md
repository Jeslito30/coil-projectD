# Optional Phase 7 mobile app

No Flutter application is implemented in this directory yet. It is not required for the baseline monitoring system, whose team dashboard is Grafana.

If Phase 7 is approved, the mobile app can consume the optional backend REST API to display two-city readings and request Wake-on-LAN actions. The REST schema and authentication flow must be defined by the Philippines pair; MQTT command/result semantics are documented in [the WoL contract](../documents/wol-contract.md).

A sent magic packet is not a successful wake. The UI must show progress states and treat only the relay's final `reachable` result as success. Real credentials, MAC addresses, VPN configuration and backend tokens must remain outside Git.
