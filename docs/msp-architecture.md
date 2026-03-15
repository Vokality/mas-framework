# Proposed MSP Architecture

This document describes the proposed managed-service-provider architecture we discussed for MAS.

It is a target system design, not the current repository implementation. It assumes the decisions we settled on earlier:

- one MAS fabric per client
- edge deployment inside each client environment
- automation allowed, but only through narrowly scoped executor agents
- MAS remains the secure control plane
- device and host protocol integrations live in connector/executor layers on top of MAS
- network infrastructure is the first optimization target, with Windows and Linux support following the same model

## 1. Recommended MSP Topology

```mermaid
flowchart TB
    subgraph msp["MSP Operations Layer"]
        noc["NOC / SOC Operators"]
        ticketing["PSA / Ticketing / ChatOps / Email"]
        portfolio["Portfolio Aggregator<br/>Cross-client summaries only<br/>No device credentials"]
    end

    subgraph clientA["Client Fabric A"]
        bridgeA["Summary Bridge / Notifier"]
        controlA["Per-client MAS Control Plane<br/>MASServer + Redis + PKI + Telemetry"]
        edgeA["Edge Agent Fleet<br/>per site / VPC / subnet"]
        devicesA["Cisco / FortiGate / switches / Windows servers / Linux servers"]
    end

    subgraph clientB["Client Fabric B"]
        bridgeB["Summary Bridge / Notifier"]
        controlB["Per-client MAS Control Plane<br/>MASServer + Redis + PKI + Telemetry"]
        edgeB["Edge Agent Fleet<br/>per site / VPC / subnet"]
        devicesB["Cisco / FortiGate / switches / Windows servers / Linux servers"]
    end

    noc --> ticketing
    ticketing --> portfolio
    bridgeA --> portfolio
    bridgeB --> portfolio

    controlA --> bridgeA
    controlB --> bridgeB

    edgeA <-->|"mTLS gRPC to MAS"| controlA
    edgeB <-->|"mTLS gRPC to MAS"| controlB

    edgeA --> devicesA
    edgeB --> devicesB
```

## 2. Per-Client Fabric

```mermaid
flowchart LR
    subgraph client["Single Client Environment"]
        subgraph sites["Client Sites / VPCs / Segments"]
            edge1["Event Ingest Agents<br/>syslog / SNMP traps / WEF"]
            edge2["Polling Connector Agents<br/>SNMPv3 / WMI / perf counters / Linux host metrics"]
            edge3["Diagnostics Connector Agents<br/>SSH / vendor API / WinRM / Linux shell access"]
            edge4["Executor Agents<br/>typed remediation only"]
        end

        subgraph control["Client MAS Control Plane"]
            core["Core Orchestrator Agent"]
            inventory["Inventory / Topology State"]
            incident["Incident / Correlation State"]
            notifier["Notifier / Summary Bridge"]
            mas["MASServer"]
            redis["Redis"]
            policy["AuthZ + DLP + Rate Limits + Circuit Breakers + Audit"]
            telemetry["OpenTelemetry / Metrics / Traces"]
        end

        vault["Client-local Credential Store<br/>vault / HSM / OS secret store"]
        ticket["Client Ticketing / Notifications"]
        net["Cisco / FortiGate / Switches"]
        win["Windows Servers"]
        linux["Linux Servers"]
    end

    edge1 --> mas
    edge2 --> mas
    edge3 --> mas
    edge4 --> mas

    mas --> policy
    mas --> redis
    mas --> telemetry

    core --> mas
    notifier --> mas
    inventory --> redis
    incident --> redis

    core --> inventory
    core --> incident
    core --> notifier

    edge1 --> net
    edge1 --> win
    edge1 --> linux
    edge2 --> net
    edge2 --> win
    edge2 --> linux
    edge3 --> net
    edge3 --> win
    edge3 --> linux
    edge4 --> net
    edge4 --> win
    edge4 --> linux

    edge2 --> vault
    edge3 --> vault
    edge4 --> vault

    notifier --> ticket
```

## 3. Logical Agent Roles

```mermaid
flowchart TB
    core["Core Orchestrator"]
    ingest["Event Ingest"]
    poll["Polling Connector"]
    diag["Diagnostics Connector"]
    exec["Executor"]
    notify["Notifier / Bridge"]

    core -->|"diagnostics.collect"| diag
    core -->|"remediation.execute"| exec
    core -->|"inventory.sync requests"| poll
    core -->|"incident updates"| notify

    ingest -->|"alert.raised"| core
    poll -->|"health.snapshot"| core
    poll -->|"inventory.sync"| core
    diag -->|"diagnostics.result"| core
    exec -->|"remediation.result"| core
    notify -->|"ticket.create / summary.publish"| external["External MSP Systems"]
```

## 4. External Protocol Surfaces

```mermaid
flowchart LR
    subgraph connectors["Connector / Executor Layer"]
        ingest["Event Ingest"]
        poll["Polling"]
        diag["Diagnostics"]
        exec["Executor"]
    end

    subgraph protocols["Management Protocols"]
        syslog["Syslog"]
        traps["SNMP Traps"]
        snmp["SNMPv3 Polling"]
        ssh["SSH / CLI"]
        api["Vendor APIs"]
        winrm["WinRM / PowerShell"]
        wmi["WMI"]
        wef["Windows Event Forwarding"]
        journald["journald / rsyslog / Syslog"]
        linuxmetrics["Linux host metrics<br/>procfs / agent / exporter"]
        sudo["SSH / sudo / systemctl"]
    end

    subgraph assets["Managed Assets"]
        firewalls["FortiGate Firewalls"]
        routers["Cisco Routers"]
        switches["Switches"]
        windows["Windows Servers"]
        linux["Linux Servers"]
    end

    ingest --> syslog
    ingest --> traps
    ingest --> wef
    ingest --> journald

    poll --> snmp
    poll --> wmi
    poll --> linuxmetrics

    diag --> ssh
    diag --> api
    diag --> winrm
    diag --> sudo

    exec --> ssh
    exec --> api
    exec --> winrm
    exec --> sudo

    syslog --> firewalls
    syslog --> routers
    syslog --> switches

    traps --> firewalls
    traps --> routers
    traps --> switches

    snmp --> firewalls
    snmp --> routers
    snmp --> switches

    ssh --> firewalls
    ssh --> routers
    ssh --> switches

    api --> firewalls
    api --> routers
    api --> switches

    winrm --> windows
    wmi --> windows
    wef --> windows
    journald --> linux
    linuxmetrics --> linux
    sudo --> linux
```

## 5. Incident, Diagnosis, And Remediation Flow

```mermaid
sequenceDiagram
    participant Device as Device / Host
    participant Edge as Edge Connector / Ingest Agent
    participant MAS as Client MAS Fabric
    participant Core as Core Orchestrator
    participant Exec as Executor Agent
    participant Ops as MSP Operator / Ticketing

    Device->>Edge: event, trap, syslog, poll response, or failed health check
    Edge->>MAS: alert.raised or health.snapshot
    MAS->>Core: deliver message through policy pipeline
    Core->>Core: correlate, deduplicate, enrich with inventory and incident state

    alt deeper diagnosis required
        Core->>MAS: diagnostics.collect
        MAS->>Edge: deliver request to diagnostics connector
        Edge->>Device: SSH / API / WinRM / SNMPv3
        Edge->>MAS: diagnostics.result
        MAS->>Core: return structured evidence
    end

    alt automated remediation allowed by policy
        Core->>MAS: remediation.execute
        MAS->>Exec: typed remediation request
        Exec->>Device: execute approved action
        Exec->>MAS: remediation.result
        MAS->>Core: deliver result
    else operator approval or notify only
        Core->>Ops: ticket, alert, recommendation, evidence bundle
    end

    Core->>Ops: final incident summary, status, and evidence
```

## 6. Trust Boundaries

```mermaid
flowchart TB
    subgraph untrusted["Managed Infrastructure Boundary"]
        devices["Routers / Firewalls / Switches / Windows / Linux"]
    end

    subgraph client["Client Security Boundary"]
        edge["Edge Connectors / Executors"]
        vault["Client-local Credential Store"]
        mas["Per-client MAS Fabric"]
    end

    subgraph msp["MSP Security Boundary"]
        noc["NOC / SOC / Ticketing"]
        portfolio["Portfolio Aggregator"]
    end

    devices --> edge
    edge --> vault
    edge <-->|"mTLS gRPC only"| mas
    mas --> portfolio
    portfolio --> noc
```

## 7. Security Properties

- Each client gets its own MAS fabric, Redis instance, certificate authority or trust domain, and edge agent fleet.
- Device credentials remain inside the client boundary and are resolved locally by connector and executor agents.
- MAS messages carry references and structured instructions, not raw credential material.
- Executors are separate from collectors so read paths and write paths can be authorized independently.
- The MSP portfolio layer receives normalized summaries, incidents, and evidence metadata only; it does not hold live device credentials or direct device reachability.
- All automation passes through MAS policy controls: allowlists, DLP, audit, rate limits, and circuit breakers.

## 8. Operational Defaults

- Realtime intake should prefer push where available: syslog, SNMP traps, and Windows event forwarding.
- Baseline health should use periodic polling: SNMPv3 for network devices, WMI/performance counters for Windows, and Linux host metrics for Linux systems.
- Deep diagnostics should be on-demand through dedicated diagnostics connectors.
- Linux management should use dedicated diagnostics and executor paths for service state, package health, disk, memory, process, and log investigation, typically via SSH plus constrained sudo/systemctl access.
- Remediation should be typed and narrow, not arbitrary shell execution.
- Cross-client oversight should happen above the tenant fabrics through a summary bridge or portfolio aggregator, not by flattening all clients into one MAS control plane.
