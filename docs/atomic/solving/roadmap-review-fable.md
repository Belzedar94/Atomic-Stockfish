# Review del weak-solution-roadmap (Fable, 2026-07-21)

Veredicto global: el plan es EXCELENTE en lo matemático-arquitectónico — al
nivel de un buen paper de ingeniería de solving. Las separaciones
claim/profile/work/attempt/fact, checkpoint≠certificate≠AcceptedFact, el
SafetyCertificate coinductivo aparte del DAG de alcanzabilidad, el tratamiento
conservador de GHI con nodos history-aware, el predicado tb_leaf_applicability
y el uso del artefacto de Los Alamos como corpus de ingeniería son decisiones
correctas y nada obvias. No cambiéis nada de eso.

Lo que sigue son las carencias y enmiendas, por orden de importancia.

## 1. El front-end no existe en el plan (y es requisito del propietario)

Una línea ("dashboard de claim/frontier/DAG" en S4) no es un producto. El
propietario quiere que la comunidad VEA el progreso y explore árboles y
evaluaciones — eso es media campaña: sin escaparate no hay cores voluntarios.
Diseño propuesto, como pieza S4-FE de primera clase:

**Explorador público del DAG de prueba** (read-only, sin login):
- Tablero (chessground, soporta atomic y es de linaje lichess) + árbol de
  jugadas navegable sincronizado. Cada nodo muestra su estado con semántica
  honesta de tres colores: PROBADO (verde, con enlace al certificado),
  REFUTADO/complement (rojo), FRONTERA (ámbar con pn/dn como "dificultad
  estimada"). El eval NNUE del oráculo se muestra en gris y SIEMPRE etiquetado
  "heurístico, no forma parte de la prueba" — la disciplina terminológica del
  plan llevada a la UI.
- Mapa de aperturas: los ~20 primeros movimientos como grid coloreado por
  estado — la foto "qué sabemos de Atomic" que la gente compartirá.
- Deep links por claim_id (`/proof/<claim>`) y badges SVG embebibles
  ("1.Nf3 f6 2.Nc3 Nh6: PROVEN — verified 2026-XX-XX") para Discord/foros.
- Descarga del certificado de cualquier subárbol cerrado + instrucciones de
  verificación local en un comando. La transparencia ES el marketing.

**Dashboard de campaña en vivo**:
- Las cajas del index actual (Cores/Nodes/Time remaining) extendidas:
  claims cerrados/día, tamaño y pendiente del frontier (sparkline), core-hours
  por contribuidor con leaderboard — cada worker VE su impacto (los chunks
  llevan atribución; gamificación sobria, el patrón SETI@home/Folding).
- Página por campaña con su gate S5 en vivo: closure rate vs generación de
  claims, la métrica de decisión del plan convertida en gráfico público.

**Implementación barata y coherente con lo ya construido**: API read-only
sobre ProofExpansion/AcceptedFact + snapshots JSON estáticos regenerados por
cron para los árboles pesados (el patrón de cacheo de las metric-boxes,
escalado); chessground + un árbol HTML server-rendered; cero frameworks
pesados. Vive en la instancia permanente actual.

## 2. Secuenciación: hay un falso prerequisito y falta valor incremental

- La recomendación final condiciona todo a "terminar Atomic-Stockfish y el
  pipeline NNUE". Innecesario: el solver usa el motor SOLO como oráculo de
  ordenación — la calidad del NNUE afecta a la eficiencia, jamás a la
  corrección. S0 (ADR de reglas), S1 (verificador) y AP-PNS/DFPN-001 pueden
  arrancar HOY en paralelo al pipeline NNUE. Son además las piezas con cero
  riesgo de desperdicio: reglas congeladas y verificador sirven igual pase lo
  que pase con el motor.
- El primer artefacto público llega tardísimo (S6/S7). Enmienda: publicar
  micro-teoremas verificados desde la semana 1 — mates conocidos de Atomic
  certificados, las cuatro FEN de fortalezas resueltas con SafetyCertificate,
  puzzles de la comunidad cerrados. Cada uno ejercita la cadena completa
  (solver→certificado→verificador→web) y puebla el explorador desde el
  primer día. Momentum comunitario = cores voluntarios para el censo S5.

## 3. Concreciones que faltan

- **Movegen externo del verificador**: el plan lo exige sin nombrarlo.
  Candidato obvio: `shakmaty` (Rust, linaje lichess, soporta atomic, es lo
  que usa lila-tablebase — independencia real de la familia Stockfish/Fairy).
  Segundo verificador: `python-chess` (variante atomic incluida). Ambos con
  commit pineado y fixtures propios, como manda el plan.
- **Postgres**: la integración v41 recién desplegada ya dejó soporte opcional
  de PostgreSQL en settings vía OPENBENCH_POSTGRES_*. El workload PROOF debe
  montarse sobre eso desde el día 1 — no reabrir la decisión.
- **Tablebases**: las 220 GiB 3-6men viven en la torre del propietario; el
  VPS tiene 40GB y NUNCA las aloja. El scheduling por capacidad TB declarada
  (ya en el plan) es la vía; añadir al manifest del worker qué familias tiene.
- **Duplicación muestral**: con verificador independiente como ancla de
  confianza, duplicar jobs solo paga para telemetría de checkpoints UNKNOWN
  (calidad de estimaciones), no para certificados — el verifier ya da la
  garantía. Ahorra un porcentaje relevante de la flota.

## 4. Expectativas — decirlo sin anestesia

La tabla de sensibilidad del propio plan: si Atomic necesita ~10^16
expansiones (lo que costó Antichess) a 1M/s por core, son ~317 core-años.
La flota actual (una torre de 32 hilos + voluntarios puntuales) tarda DÉCADAS
en eso. Los productos realistas de esta campaña, en orden:
1. La infraestructura de teoremas + el explorador (valor inmediato, imán de
   comunidad y de cómputo).
2. Teoremas de apertura certificados (el subárbol `1.Nf3 f6 2.Nc3 Nh6` como
   primera bandera plausible).
3. `startpos` solo si el censo S5 muestra pendiente favorable Y la comunidad
   crece en órdenes de magnitud (el precedente Watkins: 8 máquinas × años).
El plan ya insinúa esto ("la escala puede ser de años"); conviene que el
anuncio público lo diga igual de claro para no quemar credibilidad.

## 5. Detalles menores

- El gate S1 "un millón de secuencias make/undo" debería fijar también el
  generador de secuencias (seed + método) para que el gate sea reproducible.
- En §8, además de bytes/arista, medir bytes/certificado POR TEOREMA
  PUBLICADO — es la métrica que ve un verificador externo.
- El ledger de Discord está impecable; añadidle la conversación de 2025-09
  ("atomic solving speedrun") cuando el vault la indexe, por completitud.
