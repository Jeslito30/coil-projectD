# Optional Phase 7 backend

There is currently no backend service in this directory. The required Project D baseline uses Grafana directly as the team dashboard: **MQTT → Telegraf → InfluxDB → Grafana**. Do not treat this placeholder as a running FastAPI application.

A custom backend belongs to optional Phase 7, after the baseline deployment and acceptance tests pass. Its proposed responsibilities are:

- provide an authenticated REST API for the optional Flutter app;
- publish Wake-on-LAN commands and consume relay results using [the WoL contract](../documents/wol-contract.md);
- keep an audit log of requests and final results;
- expose the service only through the VPN, on TCP `8100`, when `ENABLE_PHASE7=1` is deliberately enabled in the firewall script.

Before implementation, the Philippines and Poland pairs must review the REST authentication/authorization model, API schema, WoL result semantics and test evidence. Port `8100` remaining closed is correct while Phase 7 is disabled.
