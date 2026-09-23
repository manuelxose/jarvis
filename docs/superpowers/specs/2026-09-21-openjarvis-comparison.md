# Comparación con OpenJarvis (open-jarvis/OpenJarvis) y plan de adopción

## Objetivo

Revisar OpenJarvis (framework de Stanford Hazy Research, 10k★, 1146 commits, Python+Rust+Tauri) y decidir qué de su diseño vale la pena portar a Jarvis v2 para ganar funcionalidad y velocidad, sin salirse del alcance actual: asistente de voz Windows-first, un solo mantenedor.

## Método

Clonado superficial de `open-jarvis/OpenJarvis` a un directorio temporal, lectura de `docs/architecture/{engine,query-flow,design-principles}.md` y del árbol `src/openjarvis/*`. Comparado contra el estado real del proyecto (vía `gsd_query`/`gsd_roadmap`, no contra `.planning/*.md`, que está desactualizado — ver nota al final) y contra el código actual en `src/jarvis/adapters/*` y `src/jarvis/application/routing.py`.

## Diferencia de escala

OpenJarvis es una plataforma de investigación para agentes locales generales: 8 agentes integrados, catálogo de skills (~13.700 vía OpenClaw), scheduler, GUI Tauri, motor Rust, optimización DSPy. Jarvis v2 es un bucle de voz de un solo usuario. La mayor parte de esa superficie no aplica aquí — importarla violaría YAGNI y el requisito V-12 (sin registries especulativos, sin broker externo).

## Qué NO adoptar (para que nadie lo re-proponga)

- **Reescritura en Rust del motor de inferencia**: justificado en OpenJarvis por throughput multi-usuario en datacenter; Jarvis v2 ya tiene su cuello de botella en audio/IO, no en el routing HTTP a Ollama/cloud. No hay medición que lo justifique.
- **Catálogo de skills (Hermes/OpenClaw) + optimización DSPy**: es la funcionalidad insignia de OpenJarvis pero resuelve un problema (marketplace de herramientas para agentes genéricos) que Jarvis v2 no tiene; su Tool Gateway ya es deliberadamente pequeño y allowlisted.
- **8 agentes / scheduler / GUI Tauri**: fuera del alcance de un asistente de voz con un turn manager y Hermes como único delegado de razonamiento complejo.

## Qué sí adoptar

### 1. Bug real encontrado y corregido: recarga de XTTS por turno

Al revisar el patrón `prepare()`/warm-up de `InferenceEngine` en OpenJarvis, comparé contra los adaptadores locales de Jarvis. `WhisperSTT` ya cachea el modelo una vez (`src/jarvis/adapters/stt/whisper.py:38-54`, arreglado en `825b84a`), pero `LocalTTS` no: `TTS("tts_models/multilingual/multi-dataset/xtts_v2")` se reconstruía dentro de cada `synthesize()`, es decir, en cada turno de conversación con TTS local — recargar un modelo XTTS-v2 completo cuesta segundos y anulaba cualquier ganancia de latencia. Corregido en `src/jarvis/adapters/tts/local.py` con el mismo patrón lazy-cache que whisper (`_load_engine()` + `self._engine`), con prueba en `tests/test_tts_provider.py::LocalTTSConstructionTests` que falla si el engine se reconstruye entre turnos. `Pyttsx3TTS.synthesize()` tiene la misma forma (`pyttsx3.init()` por llamada) pero el coste es mucho menor (SAPI, no una red neuronal); no se toca ahora — anotado abajo como candidato menor.

### 2. Esquema de telemetría por turno (`TelemetryRecord`)

OpenJarvis registra `ttft`, `latency_seconds`, tokens y coste por cada llamada de inferencia vía un wrapper (`instrumented_generate`) que publica en un event bus y persiste en SQLite. Jarvis v2 ya tiene el requisito OBS-01 (`speech_end_to_first_audio_ms` p50/p95) pendiente de instrumentación de extremo a extremo; el esquema de campos de OpenJarvis (ttft separado de latencia total, tokens, proveedor) es una referencia concreta y barata de replicar sin añadir dependencias — es un dataclass y un dict, no un framework.

### 3. Orden de descubrimiento de proveedores

`get_engine()` en OpenJarvis: proveedor pedido explícitamente → default de config → sondeo de todos los sanos. Es exactamente la forma de `ProviderChain` en `src/jarvis/adapters/models/fallback.py`, ya implementada para LLM (D018). Vale la pena extenderla al mismo criterio para STT/TTS si en el futuro hay más de un backend local compitiendo (hoy solo hay resolución por config, sin fallback de salud) — no urgente, lo dejo anotado.

### 4. Utilidades de seguridad puntuales para el Tool Gateway

`security/credential_stripper.py` y `security/ssrf.py` en OpenJarvis son utilidades pequeñas y aisladas (no todo el módulo de seguridad, que incluye sandboxing WASM y taint tracking — eso sí es sobredimensionado para este proyecto). Si Phase Tools/integraciones añade alguna herramienta que haga peticiones salientes (no las hay hoy: clipboard, archivo, control de ventana son locales), un guard SSRF de una función es barato. No hay nada que hacer ahora mismo — es una nota para cuando exista esa herramienta.

### 5. MCP (Model Context Protocol) como cliente, no ahora

OpenJarvis expone `mcp/client.py` para consumir servidores MCP como fuente de herramientas estándar. Es una idea real (evita reinventar cada integración) pero es una dependencia nueva; según Ponytail, se decide explícitamente cuando el trabajo de "Tools and integrations" empiece de verdad, no por adelantado.

## Router: no hay hueco que llenar

OpenJarvis construye un `RoutingContext` (patrones de código, longitud, urgencia) y aplica una `RouterPolicy` intercambiable para elegir modelo. Jarvis v2 ya tiene un clasificador equivalente en `src/jarvis/application/routing.py` (fast_command / fast_model / hermes por patrones deterministas, "deliberately small and conservative" por diseño). Es la misma idea con menos capas — no se recomienda añadir una ABC de `RouterPolicy` sin un caso de uso real que el regex actual no cubra.

## Nota: `.planning/*.md` en el repo está desactualizado

`.planning/STATE.md`, `ROADMAP.md`, `REQUIREMENTS.md` y `PROJECT.md` describen una fase "Phase 01 — Foundation" de un roadmap Phase 00–13 que ya no es el plan vigente: el proyecto real (vía GSD, `gsd_query`) ya completó M001–M004 (runtime lanzable, bucle de voz offline en Windows, hardening de rendimiento, routing cloud-first con fallback local) y tiene 9 requisitos activos sin milestone asignado. Los archivos `docs/superpowers/{specs,plans}/2026-09-16-*` que esos documentos referencian fueron borrados en `b0032d1` (init de GSD). No los he tocado — es limpieza de documentación, no parte de este encargo — pero conviene saberlo antes de fiarse de `.planning/` como fuente de verdad.

## Siguiente paso propuesto

Definir un milestone M005 que cubra: telemetría `ttft`/latencia total por turno (punto 2) wired a los comandos de diagnóstico existentes, y mapear los 9 requisitos activos sin milestone. Los puntos 3–5 quedan como decisiones diferidas explícitas, no como trabajo pendiente.
