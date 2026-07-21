# AtomicDB: informe de implementación (estado real)

Última actualización: 2026-07-21 (Piso 1.5). Autor: Fable (implementación end-to-end).
Spec histórica: [atomicdb-tier1-spec.md](atomicdb-tier1-spec.md) — este informe
PREVALECE sobre la spec donde difieran (la spec fue el contrato pre-implementación).
Producción: https://belzedar.duckdns.org/atomicdb/

## 1. Qué es

Solver práctico distribuido de Atomic al estilo chessdb.cn: un árbol persistente
(DAG con transposiciones fusionadas) crece desde startpos guiado por
Atomic-Stockfish, y cierra posiciones solo cuando puede demostrarlas. Estándar de
verdad: "practically solved" (categoría Solved-games de la wiki FSF), nunca
presentado como prueba teórica de juego. Principio central: **la evaluación nunca
cierra**; el eval ordena la exploración, el status solo cambia por una puerta de
cierre.

## 2. Modelos (migraciones hasta la última)

- **Position**: key sha256 de FEN canónica (contadores a cero) PK, fen, eval_cp
  (White-POV interno), status, closure (TERMINAL/MATE_PV/MINIMAX/TB), **proof**
  (ANDOR/ENGINE/DISPUTED, confianza independiente del enum de cierre), best_move
  (testigo: el mate probado más corto), won_line (PV verificada), **mate_in**
  (plies, DTM práctico), last_analysis (MultiPV con línea UCI literal), expanded,
  visits, nodes_invested, **time_invested** (segundos), priority, campaign
  (decorativo). Sin is_wall: los muros se retiraron.
- **Edge**: parent+move_uci→child, único.
- **AnalysisTask**: budget_nodes, multipv (por tarea), generation (=visita
  n-ésima, único por posición+gen), **source AUTO/USER** (USER se sirve primero),
  lease de **60 min**, PENDING/LEASED/COMPLETED.
- **Campaign**: "watched lines", solo display. **RequestLog**: rate-limit por IP.
- **WorkerPing**: presencia de workers (threads/hash/os/tasks_done) para
  `/machines/` (columna Project junto a los workers OpenBench).

## 3. Puertas de cierre y niveles de prueba

### 3.1 Niveles de prueba

AtomicDB conserva su estándar global **PRACTICAL** y los nombres históricos de
las puertas. El campo `proof` añade una segunda dimensión, explícita y
auditable, sin convertir un cierre práctico en una certificación formal:

- **ANDOR**: mate corto demostrado por búsqueda exhaustiva AND/OR del servidor.
  En turno del ganador basta una continuación probada; en turno del defensor
  se cubren todas las respuestas legales. Repeticiones en el camino invalidan
  la rama y un límite de posiciones agotado produce un resultado inconcluso.
- **ENGINE**: el servidor verificó la legalidad y terminalidad de la PV del
  motor, pero la búsqueda AND/OR agotó su presupuesto. Sigue siendo evidencia
  práctica, no una prueba exhaustiva.
- **DISPUTED**: la búsqueda exhaustiva terminó sin confirmar el mate dentro del
  horizonte. En cierres nuevos no se cierra la posición y se pide un
  reanálisis profundo; la pasada retroactiva solo marca y lista los cierres
  históricos para una decisión explícita, sin reescribirlos automáticamente.
- **VERIFIED**: confianza expuesta por la API para hojas TERMINAL y TB aceptadas
  por el servidor; no es un valor almacenado en `Position.proof`.

`manage.py verify_mates` rellena los niveles de los MATE_PV existentes de forma
reanudable y por transacciones pequeñas, usando `won_line` como hint. Reporta
los recuentos de la ejecución y los totales globales ANDOR/ENGINE/DISPUTED;
los cierres históricos sin `won_line` quedan NULL y se listan como
MISSING/UNCLASSIFIED, nunca como falsos DISPUTED. MINIMAX hereda ANDOR
únicamente cuando sus dependencias relevantes son ANDOR, TB o TERMINAL; si
depende de ENGINE, hereda ENGINE.

### 3.2 Las cuatro puertas de cierre

Las cuatro puertas de cierre son:

1. **TERMINAL** (movegen propio, pyffish; mate_in=0).
2. **MATE_PV**: el motor anuncia mate; el servidor re-verifica la PV completa
   jugada a jugada (rechaza repeticiones internas y no-terminales) y después
   intenta probarla por AND/OR. `mate_in` es la longitud de la línea testigo,
   mostrada como cota superior honesta (`≤M…`). La perspectiva de mate ya llega
   normalizada a White-POV desde el worker; el ingestor no vuelve a invertirla.
3. **MINIMAX**: retropropagación a tres valores sobre listas de movimientos
   COMPLETAS del ingestor. Victoria del mover con un hijo ganador; derrota exige
   expansión completa y todos los hijos perdidos. **DTM propagado**: mín+1 para
   el ganador (y el testigo apunta al mate probado más corto), máx+1 para el
   perdedor; un hijo sin distancia (TB) la deja en desconocida sin bloquear el
   cierre. Su nivel `proof` se hereda según las reglas anteriores.
   **Refinamiento retroactivo**: si después del cierre aparece una línea
   probada más corta, distancia y testigo mejoran y la mejora cascadea arriba.
4. **TB**: sonda WDL del worker (python-chess `AtomicBoard`) + validación de
   aplicabilidad server-side (sin enroques/ep, ≤6 piezas, contadores canónicos);
   ±2 decisivo, ±1/0 tablas prácticas. Con ≤5 piezas, el VPS vuelve a sondear el
   mismo WDL contra su set Atomic Syzygy 3-4-5 y rechaza discrepancias con un
   evento auditable. Con 6 piezas, solo acepta envíos de usuarios configurados
   en `ATOMICDB_TB_TRUSTED` o de personal `is_staff`. El prober se carga de
   forma perezosa y es inyectable en tests.

### 3.3 Tablebase server-side

El servidor dispone de su propio set Atomic Syzygy 3-4-5 en
`/opt/openbench/atomic-syzygy-345/`. La configuración de rutas y la lista de
usuarios confiables son explícitas; cargar o abrir los ficheros se difiere hasta
la primera sonda. Una respuesta TB falsa de ≤5 piezas no puede cerrar el DAG:
se rechaza y deja evidencia en `DBEvent`. El filtro de 6 piezas es deliberado
mientras el VPS no aloje ese conjunto completo.

Guardias de disciplina (todas con test): el minimax de EVALS también exige
expansión completa (aristas sueltas de /goto/ no envenenan al padre); las líneas
MultiPV del padre solo SIEMBRAN hijos sin eval, nunca pisan análisis directos.

## 4. Selector: descenso por PV con regret (estilo chessdb.cn)

`prioridad = cercanía_al_cierre − 3×regret − 1.5×visits`, donde:

- cercanía = min(|eval|,1500)/100 + 50 si |eval|≥9000 (banda de mate) + 2 si
  no expandida;
- **regret** = suma, por el mejor camino desde la raíz (Dijkstra sobre el DAG,
  las transposiciones toman la ruta óptima), de cuánto peor es cada jugada
  respecto a la mejor alternativa del minimax. Bajo la línea principal ~0; un
  opening refutado hunde a todo su subárbol. Hijos sin eval heredan el regret
  del padre (optimismo). Posiciones sin camino a la raíz (cajetín FEN):
  castigo fijo moderado.
- **Lápidas**: rama con todos los padres cerrados → priority=DEAD permanente
  (el refresh las respeta; una arista nueva vía transposición la revive).
  Sin muros: la penalización por visitas es el único backoff.

Historia: el selector v1 (|eval| sin relevancia) provocó horas de análisis bajo
1.a3 (los subárboles decididos se auto-alimentan); el regret lo corrigió — el
top de la cola pasó a ser íntegramente la línea de 1.Nf3.

## 5. Presupuestos y trabajo

### 5.1 Cola y presupuestos

- Escalera por visita: **8M → 32M → 128M → 512M → 2B** nodos. MultiPV 5 en
  visitas 0-2, **3 en las profundas**. Banda de mate salta a ≥128M.
- **bootstrap_root()**: una pasada de 2B por CADA primer movimiento (USER).
- **Request analysis** (público, sin cuenta): suelo de 128M, se sirve por
  delante de todo; rate-limit 30/h/IP + dedup + tope de cola 200. La página
  espera activamente (poll 10s, ~6 min) y se recarga sola al llegar el análisis.
- **searchmoves**: el lease adjunta las jugadas SIN resolver; el motor no gasta
  ni un nodo re-derivando defensas demostradas (validado empíricamente:
  restringe, no es pegajoso, MultiPV se autolimita). Efecto: revisitas de
  posiciones casi resueltas terminan en ~1s cerrando lo que queda.
- Lotes de lease cortos (3) para latencia de peticiones; leases de 60 min.
- **Enrutado TB**: tareas sondeables en tablebase se reservan a workers que
  declaran tenerlas (flag `tb` en el lease), salvo cola vacía.
- El refresco completo del grafo de prioridades tiene una caché por proceso de
  30 segundos. Si `backup_cascade` alcanza su guardia de 100.000 iteraciones,
  emite `DBEvent(kind='CASCADE_GUARD')` en vez de truncar en silencio.

### 5.2 Fencing de submits

La verificación AND/OR y el probe TB ocurren antes de abrir la transacción de
escritura. La fase transaccional final reclama la tarea mediante un CAS por
`id + state + machine + attempts + leased_at`; esto funciona también en SQLite,
donde `select_for_update()` por sí solo no cercaría dos submits. El estado
COMPLETED se usa de forma provisional dentro de la transacción y un fallo hace
rollback a LEASED. Solo se aceptan tareas LEASED del intento y máquina exactos,
el doble submit es éxito idempotente, los nodos se conservan exactamente
(incluido cero) y se limitan con el presupuesto autoritativo ya reclamado a
`2 × budget_nodes`. Un resultado tardío tras liberar o reasignar la lease se
rechaza; un TB rechazado no suma tiempo ni nodos. El mismo contrato cubre los
cierres enviados por `tb_wdl`.

## 6. Worker y motor

- `atomicdb_worker.py`: **archivo único autocontenido** (driver UCI embebido),
  servido en `https://belzedar.duckdns.org/atomicdb/engines/atomicdb_worker.py`
  (deploy.sh lo re-publica). Uso mínimo:
  `python atomicdb_worker.py -U user -P pass -S https://belzedar.duckdns.org -T 24`.
  La configuración T24 de la torre es intencionada; el endurecimiento no cambia
  ni arranca workers.
- **Auto-aprovisionamiento**: descarga el motor de referencia del manifest
  (sha256 verificado): exe win-x86_64 con red embebida; binario linux-x86_64 +
  red NNUE aparte (needs_net). `--engine` como override.
- Motor: Atomic-Stockfish main@1adc5239, red atomic_run3b_e202_l05.
  **SyzygyPath completo al motor** (WDL+DTZ hasta 6 piezas, 510+510 archivos);
  scores TB del motor clampeados a ±9500 (priorizan sin fingir mate). **TT
  caliente entre tareas** (sin ucinewgame). Reinicia el motor solo si casca.
- Reporta threads/hash/os/tb/elapsed; el tiempo acumulado por posición se
  muestra en el explorador.

## 7. Front-end (inglés, paleta lichess, cburnett)

- **Convención de perspectiva**: TODO se muestra desde el que mueve
  (chessdb.cn); interno White-POV. Orden: victorias del mover (mates cortos
  primero) → por score → sin analizar → derrotas (resistencia larga primero).
- Etiquetas **≤M4** (y su equivalente con signo) donde hay una distancia
  testigo; el símbolo recuerda
  que es una cota y no una DTM formal.
- **Flecha del mejor movimiento** en los tableros (en resueltas = el testigo).
- Home: métricas humanizadas (1.66B), línea de KPIs (conquista raíz, % resuelto,
  24h), **cola en vivo** ("Now analyzing" con presupuesto y máquina + "Up next"
  con líneas SAN), cajetín FEN (validación estricta pre-pyffish, creación bajo
  rate-limit), tablero interactivo con puntitos, tabla First moves compartida
  con el explorador, watched lines, milestones con línea SAN completa. La
  métrica se etiqueta **practically solved**, nunca “solved exactly”.
- Explorador: linebox SAN clicable, transposiciones, raw UCI literal (envuelto,
  con scroll), posiciones resueltas siguen explorables (filas "unexplored" via
  /goto/), botón Request con espera activa.
- **API pública**: `GET /atomicdb/api/query?fen=...` (JSON, scores del mover,
  campo mate) añade `tier='PRACTICAL'`, `trust` y
  `history_scope='COUNTERS_AND_REPETITION_IGNORED'`. TERMINAL/TB se exponen como
  VERIFIED; MATE_PV/MINIMAX exponen el nivel `proof`. Method documenta las
  puertas, ANDOR/ENGINE, el estándar práctico/GHI y lleva un ribbon textual
  PRACTICAL; contribute sigue siendo un `curl` sin toolchain.

## 8. Infraestructura

- Deploy: dev clone (rama atomicdb) → merge a spell-runner → push → deploy.sh
  (reset, pip, migrate, collectstatic, copia del worker, restart, health).
- CI: el workflow DATAGEN PostgreSQL corre la suite completa de AtomicDB sobre
  PostgreSQL real; las dependencias de reglas y probing permanecen fijadas en
  `requirements.txt`.
- Base: SQLite en **WAL** con busy_timeout 30s (OB datagen y AtomicDB comparten
  el archivo). **Backup diario** consistente (cron, 14 días, /var/backups).
  Camino a Postgres pavimentado (settings por env, CI ya lo ejercita) — cambiar
  cuando haya 3+ workers sostenidos.
- Hosting de motores: /var/www/atomicdb-engines/ + manifest.json (nginx).

## 9. Riesgos y estado honesto

1. **Las tablas son casi indemostrables** (apuesta estructural declarada): sin
   historial de repeticiones ni regla de 50 en la identidad, un DRAW de raíz
   solo llegaría por liquidación a TB. El proyecto apuesta a que atomic es
   decisivo. Limitación GHI declarada en Method.
2. **Frontera TB de 6 piezas**: los cierres de ≤5 piezas se re-verifican en el
   servidor; para 6 piezas todavía existe confianza operacional en una lista
   explícita de usuarios o personal. El manifest, reglas y política de confianza
   deben seguir versionados juntos antes de promover evidencia entre perfiles.
3. **Evals ajenos sin verificar**: submits maliciosos podrían desviar
   prioridades (no cierres, que se re-verifican). Exposición asumida a esta
   escala, igual que chessdb.cn.
4. Lecciones operativas incorporadas: barrer TODOS los procesos worker al
   reciclar (incidente del worker fantasma que secuestró leases); liberar
   leases tras reinicios; el archivo servido del worker sale del deploy.

## 10. Estado actual y siguientes pasos

El Piso 1.5 endurece la frontera de confianza sin resetear ni borrar el árbol:
signo White-POV corregido, niveles ANDOR/ENGINE/DISPUTED, re-probing TB
server-side hasta 5 piezas y fencing de leases. Postgres queda condicionado a
3+ workers sostenidos o a bloqueos recurrentes; Tier 2 (certificación formal
PNS/DFPN, [weak-solution-roadmap.md](weak-solution-roadmap.md)) sigue siendo
opcional y compatible: los subárboles cerrados del práctico son sus semillas.
