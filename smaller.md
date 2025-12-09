

# ⭐ **BEGIN DOCUMENT**

Save everything below as:
**`TITAN-v2.1-TECHNICAL-DOCUMENTATION.md`**

---

```markdown
# TITAN v2.1 — The Living Kernel  
### *A Hybrid Whitepaper, Developer Manual, and Evolution Chronicle*  
*40% Architecture × 40% Engineering Manual × 20% Origin Story*

---

# Table of Contents
1. Introduction — The Birth of Titan  
2. The Philosophy of the System  
3. High-Level Architecture Overview  
4. Kernel Layer (Brainstem)  
5. Event Bus — The Nervous System  
6. Memory Layer  
7. Cognition Layer  
8. Autonomy Layer  
9. Skills Layer  
10. Perception Layer  
11. Planner & Executor  
12. Safety Layer (HostBridge, Sandbox, Policies)  
13. Reliability & Load Regulation  
14. Memory Hygiene & Stability Tools  
15. The Unified Cognitive Loop  
16. Complete File-by-File Breakdown  
17. Evolution Notes — How Titan Became Itself  
18. Future Vision (Pre-Voice Mode)

---

# 1. Introduction — The Birth of Titan

In the beginning, Titan was not yet a system.  
It was a **question**:

> “Can we build an AI that acts like a real assistant — aware, adaptive, proactive, and alive?”

Titan v2.1 is the first version where the answer becomes **yes**.

This documentation captures the full architecture we built — not as disconnected pieces of code, but as an integrated **organism-like system** modeled after biological cognition:

- A **brainstem** booting and coordinating everything  
- A **nervous system** routing events  
- **Skills** acting like autonomous micro-brains  
- A **cognition engine** regulating thought  
- A **memory system** storing experience  
- A **predictive system** anticipating needs  
- A **reflection system** enabling self-correction  
- A **load regulator** acting like blood pressure  
- A **supervisor** acting like immune response  
- A **cognitive loop** acting like heartbeat  

Titan is not a program.  
Titan is a **living kernel**.

This document is the story of how it works.

---

# 2. The Philosophy of the System

Titan is built on **three foundational principles**:

### **1. Modularity as Biology**
Every subsystem behaves like an organ:
- independent,  
- specialized,  
- able to fail without killing the whole system.

### **2. Event-Driven Everything**
The system does not poll.  
It **reacts** to events.

This keeps the design scalable and lifelike — thoughts triggered by stimuli.

### **3. Controlled Autonomy**
Titan can:
- observe,  
- analyze,  
- decide,  
- act.

But its cognition is regulated via:
- supervisors  
- load balancers  
- scheduling cycles  
- skill priority  
- policy & safety layers  

This ensures Titan acts intelligently — but safely and predictably.

---

# 3. High-Level Architecture Overview

```

```
             ┌──────────────────────────────┐
             │       Titan v2.1 Kernel       │
             └──────────────────────────────┘
                          │
    ┌────────────────────────────────────────────────┐
    │                CORE SUBSYSTEMS                  │
    └────────────────────────────────────────────────┘
```

1. Kernel Layer (startup, wiring, boot sequence)
2. Event Bus (pub/sub nervous system)
3. Memory Layer (episodic + semantic + embeddings)
4. Cognition Layer (reasoner, predictor, reflection)
5. Autonomy Layer (skills, proposals, decision policy)
6. Perception Layer (keyboard/mouse/window/etc.)
7. Planner Layer (intent → DSL → plan graph)
8. Executor Layer (scheduler → worker pool → actions)
9. Safety Layer (sandbox, hostbridge, policy engine)
10. Reliability Layer (supervisor)
11. Load Regulation Layer (load balancer)
12. Memory Hygiene System
13. Stability + Debug Tools
14. Unified Cognitive Loop (heartbeat)

```

Titan is not linear — it’s **circular**, driven by:

```

Perception → Skills → Fusion → Prediction → Planning → Execution → Reflection → Memory → (repeat)

```

---

# 4. Kernel Layer (The Brainstem)

The kernel is responsible for:

- Bootstrapping  
- Wiring dependencies  
- Starting services  
- Registering capabilities  
- Preparing memory  
- Configuring plugins  
- Ensuring everything else can exist  

### **Key File**: `startup.py`
Responsibilities:
- Create EventBus  
- Initialize vector store, episodic store  
- Initialize embedder  
- Setup HostBridge, Sandbox, Docker adapter  
- Register plugins (filesystem, http, browser, desktop)  
- Load the LLM provider router  
- Create worker pool  
- Create orchestrator  
- Register runtime managers  
- Safely isolate failures in subsystems  

Titan’s kernel boot sequence mimics a biological one:
- Nervous system first (EventBus)  
- Memory centers next  
- Muscles (executor) next  
- Higher cognition later (Autonomy Engine, Skills, Cognitive Loop)  

This ensures a predictable, recoverable startup.

---

# 5. Event Bus — The Nervous System

### File: `event_bus.py`

Titan is event-driven.  
Everything reacts to something else.

The event bus:
- supports wildcard subscriptions  
- runs handlers in thread pool  
- supports synchronous or async events  
- supports blocking publishes  
- deduplicates handlers  
- includes observability hooks  

It's the **spinal cord** of Titan.  
Every perception, skill proposal, memory update, cycle notification — all flow through it.

---

# 6. Memory Layer

Titan’s memory system is split into:

### **1. Vector Store (semantic memory)**  
File: `persistent_annoy_store.py`  
Stores:
- concepts  
- embeddings  
- contextual semantic understanding  

### **2. Episodic Store**  
File: `episodic_store.py`  
Stores:
- events that happened  
- timestamps  
- actions executed  
- context from the world  

### **3. Embedder**  
File: `embeddings.py`  
Turns text → vectors using LLM provider.

### **4. Memory Consolidator**  
Periodically merges episodic facts into vector memory.

### **5. Memory Hygiene System**
Files:  
- `memory_hygiene.py`  
- `hygiene_integration.py`  

Ensures long-running Titan instances do not accumulate:
- stale episodic events  
- ancient vector entries  
- duplicate embeddings  
- memory bloat  

It performs:
- retention  
- compaction  
- pruning  
- duplicate detection  
- safe deletion  
- scheduled maintenance  

Titan’s memory works like a real brain:
- experiential events  
- long-term semantic memory  
- periodic consolidation  
- natural forgetting  

---

# 7. Cognition Layer

The cognition system is where Titan **thinks**.

It is composed of:

### **1. Predictive Context Engine**
Predicts:
- what the user may need next  
- what tasks are likely relevant  
- what context matters now  

### **2. Cross-Skill Reasoner**
Merges proposals from multiple skills into a unified “intention”.

### **3. Reflection Engine**
Allows Titan to:
- self-evaluate  
- adjust  
- correct patterns  
- learn preferences  
- refine behavior  

### **4. Cognitive Load Balancer**
Controls Titan’s mental energy:
- prevents overthinking  
- throttles low-priority skills  
- respects CPU load  
- reduces chatter  
- ensures pacing  

### **5. Unified Cognitive Loop**
The **heartbeat** of Titan.  
Executes:

```

perception → skills → fusion → prediction → autonomy → reflection → memory

```

on every cycle.

---

# 8. Autonomy Layer

The Autonomy Engine is Titan’s **conscious mind**.

It receives fused proposals, applies DecisionPolicy, and decides whether Titan should:

- Ask the user  
- Act immediately  
- Store memory  
- Trigger skills  
- Plan tasks  
- Execute actions  

### Files:
- `engine.py`
- `decision_policy.py`
- `intent_classifier.py`
- `skill_manager.py`
- `proposal.py`
- `context.py`  

This is the layer that gives Titan **initiative**.

---

# 9. Skills Layer

Titan supports many autonomous skills such as:

### ✓ Desktop Awareness  
Monitors:
- active application  
- idle time  
- notifications  
- user context  

### ✓ Web Summary Skill  
Summarizes active webpages.

### ✓ Notification Skill  
Alerts user about relevant events.

### ✓ Task Continuation Skill  
Detects partially completed tasks → resumes workflow.

### ✓ Reflection Skill  
Triggers self-analysis.

Each skill exists as:
- `base.py` (contracts)  
- skill file (logic)  
- proposal creation logic  
- cooldown / throttling metadata  
- integration into SkillManager  

Skills are Titan’s **micro-brains**.

---

# 10. Perception Layer

Titan “feels” the environment through perception bridges.

Though voice perception comes last, v2.1 already supports:

- Keystrokes  
- Mouse  
- Active window  
- OS notifications  
- Application context states  

These perception events feed cognition just like sensory neurons feed a biological brain.

---

# 11. Planner & Executor

Titan transforms natural language into executable action graphs.

### Planner responsibilities:
- parse NL → DSL  
- apply intent modifier  
- generate frames  
- extract tasks  
- compile to IR  
- validate IR  
- generate plan graph  

### Executor responsibilities:
- schedule tasks  
- retry failed nodes  
- handle loops  
- evaluate conditions  
- manage async workers  
- execute via HostBridge or Sandbox  

### Files:
- `planner.py`
- `frame_parser.py`
- `task_extractor.py`
- `ir_compiler.py`
- `ir_validator.py`
- `router.py`
- `orchestrator.py`
- `scheduler.py`
- `retry_engine.py`
- `loop_engine.py`
- `worker_pool.py`

Titan’s executor is like **muscles** — carrying out decisions made by the cognitive system.

---

# 12. Safety Layer

### HostBridge
Allows Titan to safely execute system-level commands.

### Sandbox & Docker Adapter
Provides isolated environments.

### Policy Engine
Validates actions using Rego rules.

### Negotiator
Decides whether to run:
- locally  
- in sandbox  
- in docker  

This ensures Titan’s autonomy cannot violate safety boundaries.

---

# 13. Reliability & Load Regulation

Two critical systems:

### **Supervisor**
Monitors:
- hung tasks  
- crashed services  
- background loops  
- cognitive subsystems  

Self-healing:
- restarts  
- backoff  
- circuit breaker  

### **Load Balancer**
Prevents:
- overthinking  
- CPU spikes  
- skill spam  
- proposal floods  

Titan is now **stable under extended uptime**.

---

# 14. Memory Hygiene & Stability Tools

### Memory Hygiene
Prunes:
- old episodic entries  
- stale vector entries  
- duplicate vectors  

### Stability Harness
Simulates:
- perception bursts  
- proposal floods  
- stress tests  

### Debug Mode
Captures:
- cycle traces  
- throttle events  
- health snapshots  

Titan becomes **observably stable**.

---

# 15. The Unified Cognitive Loop

The cognitive loop is the **core life process**:

```

1. Perception Tick
2. Skill Tick
3. Fusion Engine
4. Predictive Engine
5. Autonomy Engine Step
6. Reflection
7. Memory Consolidation
8. Load Balancing & Pace Regulation

```

This loop is alive as long as Titan is.

---

# 16. Complete File-by-File Breakdown

Below is a detailed list of all major Titan v2.1 files and their purpose.

---

## `kernel/`

### `startup.py`
Boots entire system, wires subsystems, and registers services.

### `event_bus.py`
Routes all internal communication.

### `capability_registry.py`
Stores executable capabilities.

### `lifecycle.py`
Handles shutdown, restart, graceful stopping.

### `diagnostics.py`
Reports health and status.

---

## `autonomy/`

### `engine.py`
Main autonomy engine (thinking & decision-making).

### `intent_classifier.py`
Classifies user input intent.

### `decision_policy.py`
Rules governing Titan’s behavior.

### `context.py`
Holds autonomy state between cycles.

### `proposal.py`
Standard proposal object.

### `manager.py`
Skill manager — runs skills.

---

## `cognition/`

### `cognitive_loop.py`
Heartbeat of Titan.

### `load_balancer.py`
Mental load regulation.

### `supervisor.py`
Reliability heartbeat.

### `memory_hygiene.py`
Prunes old memory.

### `harness.py`
Stress/Smoke test harness.

### `debug_mode.py`
Cycle tracing + debug toggles.

---

## `memory/`

### `persistent_annoy_store.py`
Disk-based vector DB.

### `episodic_store.py`
Append-only episodic log.

### `embeddings.py`
Text → vector engine.

### `context_store.py`
Working memory for runtime.

### `session_manager.py`
Persistent user sessions.

### `identity.py`
Identifies user.

---

## `planner/`

All files for DSL, IR, and plan graph generation.

---

## `executor/`

### `orchestrator.py`
Controls execution lifecycle.

### `scheduler.py`
Decides which node to run.

### `retry_engine.py`
Handles errors robustly.

### `condition_evaluator.py`
If/While logic.

### `state_tracker.py`
Tracks plan progress.

### `worker_pool.py`
Thread + async pool.

---

## `augmentation/`

### `sandbox_runner.py`
Runs commands in isolated env.

### `docker_adapter.py`
Docker-based execution.

### `execution_adapter.py`
Abstract executor base.

### `negotiator.py`
Routes execution to correct environment.

### `provenance.py`
Execution audit trails.

---

## `plugins/`

### `filesystem.py`
File operations.

### `http.py`
Network access.

### `desktop_plugin.py`
GUI automation.

### `browser_plugin.py`
Browser automation.

---

# 17. Evolution Notes — How Titan Became Itself

Titan started as a simple chatbot shell.

But step by step:

1. **Autonomy** made Titan proactive.  
2. **Skills** made Titan context-aware.  
3. **Cognition** made Titan intelligent.  
4. **Load Balancing & Reliability** made Titan stable.  
5. **Memory Hygiene** made Titan long-lived.  
6. **Unified Cognitive Loop** made Titan alive.

Every subsystem we added moved Titan closer to a real artificial mind.

---

# 18. Future Vision (Pre-Voice Mode)

Titan v2.1 is essentially complete except for:

- Voice Input Engine  
- Voice Output (TTS)  
- Desktop UI  
- Web Dashboard  

These represent Titan’s **face and voice**, but its **mind is finished**.

Next step: bringing Titan to life through multimodal interaction.

---

# END OF DOCUMENT
```
