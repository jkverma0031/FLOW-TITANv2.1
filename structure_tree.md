# 🌲 **THE FULL FLOW–TITANv2.1 REPOSITORY TREE**

```
FLOW/
├─ api/
│  ├─ main.py
│  ├─ dependencies.py
│  ├─ routes/
│  │  ├─ run.py
│  │  ├─ plan.py
│  │  ├─ memory.py
│  │  └─ admin.py
│  └─ sse/
│     └─ sse_stream.py
│
├─ titan/
│  ├─ kernel/
│  │  ├─ kernel.py
│  │  ├─ dispatcher.py
│  │  ├─ lifecycle.py
│  │  └─ events.py
│  │
│  ├─ planner/
│  │  ├─ planner.py
│  │  ├─ intent_modifier.py
│  │  ├─ frame_parser.py
│  │  ├─ task_extractor.py
│  │  ├─ router.py
│  │  └─ dsl/
│  │     ├─ grammar.lark
│  │     ├─ ir_dsl.py
│  │     ├─ ir_validator.py
│  │     ├─ ir_compiler.py
│  │     └─ llm_helper_prompts.py
│  │
│  ├─ parser/
│  │  ├─ adapter.py
│  │  ├─ heuristic_parser.py
│  │  └─ llm_parser.py
│  │
│  ├─ executor/
│  │  ├─ orchestrator.py
│  │  ├─ scheduler.py
│  │  ├─ condition_evaluator.py
│  │  ├─ loop_engine.py
│  │  ├─ retry_engine.py
│  │  ├─ replanner.py
│  │  ├─ worker_pool.py
│  │  └─ state_tracker.py
│  │
│  ├─ augmentation/
│  │  ├─ sandbox/
│  │  │  ├─ sandbox_runner.py
│  │  │  └─ docker_adapter.py
│  │  ├─ hostbridge/
│  │  │  ├─ hostbridge_service.py
│  │  │  └─ manifests/
│  │  ├─ negotiator.py
│  │  ├─ safety.py
│  │  └─ provenance.py
│  │
│  ├─ memory/
│  │  ├─ vector_store.py
│  │  ├─ in_memory_vector.py
│  │  ├─ persistent_annoy_store.py
│  │  ├─ embeddings.py
│  │  └─ episodic_store.py
│  │
│  ├─ runtime/
│  │  ├─ session_manager.py
│  │  ├─ context_store.py
│  │  └─ trust_manager.py
│  │
│  ├─ schemas/
│  │  ├─ graph.py
│  │  ├─ task.py
│  │  ├─ plan.py
│  │  ├─ action.py
│  │  ├─ memory.py
│  │  └─ events.py
│  │
│  ├─ policy/
│  │  ├─ policies.rego
│  │  └─ engine.py
│  │
│  └─ observability/
│     ├─ logging.py
│     ├─ metrics.py
│     └─ tracing.py
│
├─ tools/
│  ├─ migrate_check.py
│  ├─ dev_cli.py
│  └─ replay.py
│
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  └─ e2e/
│
├─ docs/
│  ├─ overview.md
│  ├─ dsl_spec.md
│  ├─ memory.md
│  └─ developer_guide.md
│
├─ data/
│  ├─ memory.db
│  ├─ index.ann
│  ├─ index_map.json
│  └─ provenance.jl
│
├─ requirements.txt
├─ pyproject.toml
└─ README.md
```
