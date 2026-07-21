# AtomicDB — spec del Piso 1: solver progresivo con cierre exacto

Estado: SPEC aprobada por el propietario (2026-07-21). Sustituye como PRIMER
proyecto al roadmap formal (`weak-solution-roadmap.md`), que queda como Piso 2
opcional; ver §9 para el puente. Redirección de prioridades para el agente.

> **AVISO (2026-07-21, tarde): el Piso 1 está IMPLEMENTADO y en producción.**
> Este documento es el contrato histórico; el estado real vive en
> [atomicdb-implementation-report.md](atomicdb-implementation-report.md), que
> PREVALECE donde difieran. Desviaciones principales decididas por el
> propietario durante la implementación:
> - §4.1: el selector es descenso por PV con **regret desde la raíz**
>   (estilo chessdb.cn), no la fórmula original.
> - §4.2 **campañas**: retiradas como ámbito de búsqueda (quedan como
>   "watched lines" decorativas). §4.3 y §6.5 **muros**: eliminados del todo.
> - §1: producción corre SQLite+WAL (Postgres queda como camino de escalado,
>   ya ejercitado en CI).
> - §6.7: existe como "Request analysis" **público sin cuenta** (rate-limit
>   por IP), con suelo de 128M nodos y espera activa en la página.
> - Escalera de presupuestos: 8M→2B nodos (no la original), con searchmoves
>   restringido a jugadas vivas y DTM (mate_in) propagado con testigos.

## 0. Objetivo y estándar de verdad

Construir el equivalente de chessdb.cn para Atomic: un **árbol persistente y
compartido** que el motor va profundizando nodo a nodo (siempre desde la
posición del nodo, nunca re-buscando desde startpos), con retropropagación
minimax de evals y de **estados exactos**, hasta cerrar aperturas enteras.

Estándar de verdad de este piso: **"resuelto en la práctica"** (la categoría
de la wiki Solved-games de Fairy-Stockfish). Un subárbol cerrado aquí es un
teorema práctico, no una prueba game-theoretic — y la UI, los anuncios y la
base de datos lo etiquetan SIEMPRE así. La lección del precedente
atomic-crazyhouse (ubdip rehusó llamarlo solved por no verificable) se hereda
como política editorial. Lo que sí endurecemos frente a chessdb clásico:
ningún nodo se cierra por eval — solo por las vías exactas de §3.

Dimensionado honesto: pensado para 1-3 máquinas (la torre de 32 hilos como
base) más voluntarios ocasionales. Nada del diseño asume granja grande.

## 1. Modelo de datos (PostgreSQL — el soporte v41 ya desplegado)

```
Position(
  key           BYTEA PK,     -- SHA-256 de fen_canónica (ver §3.5)
  fen           TEXT,
  eval_cp       INT NULL,     -- heurístico, perspectiva blanca
  status        ENUM(UNKNOWN, WHITE_WIN, BLACK_WIN, DRAW),  -- exacto
  closure       ENUM(NULL, TB, MATE_PV, MINIMAX),           -- por qué cerró
  best_move     TEXT NULL,
  depth_invested INT, nodes_invested BIGINT, visits INT,
  is_wall       BOOL DEFAULT FALSE,       -- pozo detectado (§4.3)
  updated       TIMESTAMPTZ)
Edge(parent_key, move_uci, child_key, PK(parent_key, move_uci))
Campaign(id, name, root_key, status, created)     -- p.ej. "Nf3-f6-Nc3-Nh6"
AnalysisTask(id, position_key, budget_nodes, multipv, status,
             lease/attempts/machine, created, completed)     -- patrón DATAGEN
Event(id, ts, kind, payload_json)   -- milestones para el feed público
Contributor stats: reutiliza Profile + agregados por máquina.
```

Los datos heurísticos (eval, best_move) y los exactos (status, closure) viven
en columnas separadas y NUNCA se mezclan en la lógica: eval ordena la
exploración; status cierra subárboles.

## 2. Ciclo del sistema

```
selector -> AnalysisTask(pos, budget) -> worker OpenBench
       worker: setoption MultiPV k; go nodes N desde pos
       devuelve: k jugadas con eval + FENs hijos + PV de mate si la hay
              + probes TB de hijos si tiene las tablas
ingesta -> upsert hijos y aristas -> cierres locales (§3) -> backup minimax
        -> refresco de prioridades -> selector ...
```

Todo idempotente: task_id único por (position_key, budget, generación);
leases con expiración y re-reparto (la maquinaria DATAGEN ya probada).

## 3. Semántica de cierre exacto

### 3.1 Cierre por tablebase (TB, 6 piezas)
Un nodo con ≤6 piezas cierra con el resultado WDL si pasa el
`tb_applicability_lite` (heredado simplificado del roadmap formal):
sin derechos de enroque (si hay, se expande), en-passant se expande en vez de
probar, y el contador 50-mov no contradice la conversión (si WDL=win con
rule50 alto → se expande hasta que DTZ confirme). Los workers declaran qué
familias TB tienen; el selector les enruta frentes con material bajo.

### 3.2 Cierre por mate verificado (MATE_PV)
Un score de mate del motor NO cierra por sí solo (TT/poda pueden mentir).
Cierra cuando el ingestor **reproduce la PV completa jugada a jugada** con
su propio movegen (pyffish vale en este piso): legalidad de cada jugada,
terminal real al final (explosión del rey / mate / ahogado). Coste: trivial.
Cobertura: el 90%+ de los cierres tácticos de Atomic serán de este tipo.

### 3.3 Cierre por minimax (MINIMAX)
Retropropagación estándar de tres valores con el que mueve:
- Si alguna arista lleva a hijo con status ganador para el que mueve → cerrado.
- Si TODAS las aristas conocidas llevan a estados perdedores para el que
  mueve Y la lista de aristas está completa (movegen del ingestor, no del
  worker) → cerrado perdedor. La completitud de jugadas la garantiza el
  ingestor generándolas todas al expandir — el motor jamás filtra la lista
  (con test de regresión de completitud propio).
- DRAW se propaga análogamente (mejor alcanzable = tablas exactas).

### 3.4 Tablas y repetición (limitación declarada del piso 1)
DRAW exacto solo entra por: TB draw, ahogado/insuficiencia verificados, o
tablas por 50-mov/triple repetición DENTRO de una PV verificada. La
path-dependence fina (GHI) no se modela: una posición se identifica sin
historial (§3.5). Consecuencia documentada: un WHITE_WIN práctico podría en
teoría apoyarse en un ciclo mal contado — mitigación: el verificador de PVs
rechaza PVs con repetición interna, y los cierres MINIMAX exigen aristas a
cierres que terminan (TB/MATE_PV como pozos del DAG). El caso residual se
acepta como riesgo del estándar "práctico" y queda listado en /method.

### 3.5 Identidad de posición
`fen_canónica` = piezas + turno + enroques + ep-solo-si-capturable. SIN
contadores. Transposiciones se fusionan (es la gracia del DAG y el ahorro
grande de Atomic, rico en transposiciones de apertura).

## 4. Selector de frontera

### 4.1 Prioridad de un nodo UNKNOWN
```
prio = w1 * cercanía_al_cierre      -- |eval| alto y subiendo: oro
     + w2 * impacto                 -- tamaño del subárbol que cerraría
     + w3 * frescura                -- castigo a re-visitas sin progreso
     + w4 * boost_manual            -- campañas y sugerencias (§6.7)
     - w5 * castigo_muro            -- §4.3
```
Presupuesto por visita: escalera 100k → 1M → 10M nodos según visitas previas
(re-visitar con más profundidad, el patrón chessdb).

### 4.2 Modo campaña
Una Campaign fija una raíz (p.ej. tras `1.Nf3 f6 2.Nc3 Nh6`) y el selector
restringe el frente a su subárbol hasta cerrarlo o declararlo muro. Orden
inicial de campañas: `...Nh6` primero, `...c6` después, luego el abanico de
`1.Nf3`. Arranque limpio: sin corpus heredados ni seeds externos — el árbol
se construye entero desde el motor actual.

### 4.3 Muros (nodos estancados)
Un nodo con ≥N visitas (def. 5) y escalera agotada sin cambio de status ni
mejora de eval se marca `is_wall`. Los muros salen del frente automático,
aparecen en la página pública de Muros (§6.5) y solo se reabren por boost
manual o por cierre de un vecino: el estancamiento se convierte en una lista
de trabajo visible en vez de en un sumidero de cómputo.

## 5. Workload ANALYSIS en OpenBench

Cuarto tipo de workload (junto a GAMES/TUNE/DATAGEN), mismos patrones:
- Task = {fen, budget_nodes, multipv, tb_required: bool}. Grueso: lotes de
  ~50-200 tasks por lease para amortizar (un lote ≈ 10-30 min).
- El worker usa Atomic-Stockfish tal cual (UCI: position fen / go nodes /
  MultiPV). Sin build especial. Devuelve JSON por task; upload comprimido
  con sha256 (mecanismo DATAGEN reutilizado).
- Fallo/lease/reintento/etiquetado en el log del cliente: idéntico a DATAGEN.
- Prioridad configurable frente a SPRT/DATAGEN en la cola normal.

## 6. Front-end — "AtomicDB Explorer" (pieza de primera clase)

Objetivo: que un entusiasta de Atomic SIN cuenta pueda perderse una tarde
explorando el árbol, entender el estado del proyecto en 10 segundos, y
compartir descubrimientos. Estética lichess-like: limpia, oscura por defecto
(toggle claro), tipografía grande, cero frameworks pesados (Django templates
+ chessground + JS vanilla/htmx; los datos calientes por snapshots JSON
cacheados 30-60s, patrón de las metric-boxes).

### 6.1 Home — "el mapa de la conquista"
- Hero: tablero-mapa de los 20 primeros movimientos blancos como GRID de
  teselas coloreadas: verde (WHITE_WIN cerrado), rojo (BLACK_WIN), azul
  (DRAW), ámbar con intensidad por |eval| (frontera). Cada tesela: jugada,
  eval retropropagado, % del subárbol cerrado. Click → Explorer.
- Debajo, 4 contadores grandes (patrón metric-boxes): posiciones en el árbol,
  subárboles cerrados, nodos-motor invertidos, muros activos.
- Feed de hitos (Event): "2026-07-2X — `2...Nh6 3.g4 e5` CERRADO
  (WHITE_WIN práctico, 14.302 posiciones, cerrado por worker rainrat)".
- Barra de progreso por campaña activa: % de defensas negras cerradas.

### 6.2 Explorer — la página central
Layout de 3 columnas (colapsable a 1 en móvil):
- **Izquierda: tablero** (chessground, tema atomic): flechas del best_move
  (sólida) y alternativas (finas, opacidad por eval); al reproducir capturas,
  animación breve de explosión (los entusiastas lo ESPERAN); botón de girar;
  input FEN/pegar PGN para saltar a cualquier posición del árbol.
- **Centro: la mesa de jugadas** del nodo actual — una fila por jugada legal:
  chip de status (● verde/rojo/azul/ámbar + icono, colorblind-safe), jugada
  SAN, eval con MINI-BARRA horizontal (blanco/negro), nodos invertidos,
  tamaño del subárbol, badge TB/MATE cuando el hijo cerró exacto. Orden por
  status→eval. Fila superior fija con el veredicto del nodo actual:
  "UNKNOWN — mejor línea +6.4" o "WHITE_WIN (práctico) via MATE_PV".
  Las jugadas SIN analizar aparecen en gris con eval "—" (transparencia:
  se ve exactamente qué falta).
- **Derecha: contexto**: breadcrumb clicable de la línea (`1.Nf3 f6 2.Nc3`),
  PV principal con autoplay, transposiciones entrantes ("también se llega
  por..."), y la ficha del nodo (visits, última visita, presupuesto usado,
  botón "sugerir análisis" §6.7).
- Navegación por teclado (←→ jugadas, ↑↓ alternativas), URLs profundas
  `/explore/<key>` estables y compartibles.

### 6.3 Compartir (la palanca social)
- Botón share en cada nodo: copia URL + genera tarjeta PNG/SVG (tablero +
  línea + veredicto + logo) lista para Discord/Twitter.
- Badges SVG embebibles por campaña/nodo: `img.shields`-style
  "2...Nh6 — SOLVED (practical)" con deep-link.

### 6.4 Progress — para los que aman los gráficos
- Series temporales (SVG estático regenerado por cron, sin JS pesado):
  posiciones/día, cierres/día, tamaño del frente, distribución de evals del
  frente (histograma que "migra" hacia el cierre — la foto del progreso).
- Tabla de campañas: raíz, defensas totales/cerradas, ETA ingenua por
  pendiente reciente (con el disclaimer honesto del Time-remaining actual).

### 6.5 Walls — la página de los muros
Tabla ordenable de pozos: miniatura del tablero, eval estancado, visitas,
nodos quemados, "atascado desde". Invitación explícita: "¿ves el plan que el
motor no ve? Sugiere una jugada". Es la página que convertirá a los jugadores
fuertes de Atomic en colaboradores — el motor les
pide ayuda exactamente donde los humanos aún aportan.

### 6.6 Contributors
Leaderboard (nodos aportados, cierres desbloqueados, muros rotos — este
último el trofeo gordo), perfil por usuario con sus hitos. Opt-in de nombre
visible. Sobrio: sin puntos inflados, los números reales ya son el juego.

### 6.7 "Sugerir análisis" (usuarios registrados)
En cualquier nodo: proponer una jugada o pedir profundización → crea un
boost de prioridad (w4) con rate-limit por usuario/día y cola visible de
sugerencias con su resultado. Cierra el bucle humano-motor y da a los
entusiastas agencia real sin comprometer la disciplina de cierre.

### 6.8 Method — la página de honestidad
Qué significa "práctico", las tres vías de cierre, la limitación GHI (§3.4),
enlace al roadmap formal del Piso 2. La credibilidad es un feature.

## 7. Hitos y gates (cada uno falsable, estilo casa)

- **M1 (núcleo, ~1 semana)**: schema + ingestor + backup + cierres §3 + CLI
  local single-worker. GATE: suite de mates conocidos de Atomic cierra con
  los resultados esperados; un set propio de fortalezas sintéticas NO cierra;
  test de completitud de movegen (ninguna jugada legal omitida en expansión);
  backup determinista bajo replay.
- **M2 (distribuido, ~1 semana)**: workload ANALYSIS + leases + lotes; 2
  workers concurrentes sin dobles-conteos bajo kill/retry adversarial.
  GATE: mismo árbol final con 1 y con 2 workers (modulo orden).
- **M3 (público)**: Explorer + Home + Progress read-only en
  belzedar.duckdns.org. GATE: navegable de punta a punta con datos de M1/M2;
  snapshots cacheados (cero queries pesadas por pageview).
- **M4 (primera bandera)**: campaña `1.Nf3 f6 2.Nc3 Nh6` + Walls +
  sugerencias. GATE: pendiente de cierre medible y positiva del frente; el
  primer teorema práctico publicado con su tarjeta compartible.

## 8. Riesgos y respuestas

| Riesgo | Respuesta |
|---|---|
| Fortalezas/nodos estancados | §4.3: muros visibles + TB 6-men + ayuda humana dirigida |
| Mate-scores falsos del motor | Solo cierra PV verificada jugada a jugada (§3.2) |
| Ciclos/GHI en el estándar práctico | Limitación declarada (§3.4) + PVs sin repetición interna + pozos del DAG exactos |
| Poca contribución externa | Diseñado para 1-3 máquinas; lo social (walls, share, sugerencias) es upside, no dependencia |
| DB crece | Posiciones son ~200B; 100M posiciones ≈ 20GB en Postgres — años de margen |

## 9. Puente al Piso 2 (el roadmap formal)

Nada se tira: el árbol de AtomicDB es exactamente el "oráculo de ordenación"
que la arquitectura PNS/DFPN del roadmap necesita; los subárboles prácticos
cerrados son los candidatos ideales a certificación; y walls+census dan los
datos de viabilidad que su fase S5 pedía. Si algún día hay compute/comunidad
para certificar, el Piso 2 arranca con ventaja en vez de desde cero.
