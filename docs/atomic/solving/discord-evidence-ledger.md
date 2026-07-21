# Ledger de Discord y antecedentes para resolver Atomic Chess

Fecha de corte: 2026-07-13
Ámbito: exportación histórica del servidor Fairy-Stockfish, literatura primaria de game solving y consecuencias implementables para `AtomicSolver` / `OpenBench Proof`.

Documento complementario: [hoja de ruta de weak-solving](./weak-solution-roadmap.md).

## 1. Método y frontera de confianza

- Se consultó una exportación local completa mediante `fairy-vault`; no se usaron la API de Discord ni credenciales.
- El corpus contiene 49.996 mensajes únicos, 20 canales, 15 threads y 1.802 mensajes con adjuntos.
- Los JSONL raw contienen más filas porque `channel_791247944463417374.jsonl` repite al menos un bloque completo unos 5.300 renglones después. Toda conclusión se deduplicó por message ID.
- Discord se usa como memoria histórica: identifica intentos, posiciones, artefactos y consejos. No demuestra el valor game-theoretical de ninguna posición.
- Cada conclusión de diseño se contrastó con literatura primaria o se etiquetó como propuesta del proyecto.

## 2. Ledger de evidencia comunitaria

### 2.1 El consejo de ubdip es inequívoco: motor-oráculo + PNS + subárboles

| Fecha UTC | Autor / message ID | Evidencia exacta | Consecuencia |
|---|---|---|---|
| 2021-01-24 | belzedar_ `802962819049717800` | [general](https://discord.com/channels/779317816897699850/779317816897699854/802962819049717800): “It would be awesome if we had a standard solver machine. I imagine it similar to fishtest, so we can cluster as many cpu power as needed”; el [mensaje siguiente](https://discord.com/channels/779317816897699850/779317816897699854/802962902884810752) lo aplica a variantes con potencial de ser resueltas | Antecedente explícito de un scheduler comunitario distribuido para solving, no solo de un clúster local |
| 2021-01-26 | ubdip `803658203891630150` | [nnue-general:338](https://discord.com/channels/779317816897699850/784418118503235625/803658203891630150): la rama cluster basada en TT se limita a clúster local, no distributed computing abierto | No confundir cluster HPC con OpenBench WAN |
| 2021-03-08 | ubdip `818442571582930979` | [analysis:94](https://discord.com/channels/779317816897699850/812407482369441813/818442571582930979): un engine fuerte reduce mucho la complejidad al señalar la jugada del ganador, pero no es una prueba estricta | Atomic-Stockfish es oracle de ordenación, nunca autoridad de cierre |
| 2021-03-08 | ubdip `818443628808372225` | [analysis:99](https://discord.com/channels/779317816897699850/812407482369441813/818443628808372225): recomienda proof-number search para convertir el motor en solver estricto | PNS de referencia y DFPN como primer backend |
| 2021-03-08 | ubdip `818447247464464394` | [analysis:101](https://discord.com/channels/779317816897699850/812407482369441813/818447247464464394): Fairy podría ser framework de solving, pero una prueba científica requiere mucha diligencia | Verificador y certificado son productos de primera clase |
| 2021-07-22 | ubdip `867659752875491388` | [off-topic:151](https://discord.com/channels/779317816897699850/793813826436464640/867659752875491388): resultados prácticos con hash collisions no pasarían peer review; Antichess es el modelo correcto | No publicar «solved» sin proof artifact verificable |
| 2022-06-13 | ubdip `985992353372307516` | [general:5468](https://discord.com/channels/779317816897699850/779317816897699854/985992353372307516): distingue PNS exhaustivo, alpha-beta casi exhaustivo y árbol crítico de eval alta | El roadmap mantiene etiquetas separadas para prueba, practical solve y análisis |
| 2024-06-06 | ubdip `1248179697561370655` | [analysis:1776](https://discord.com/channels/779317816897699850/812407482369441813/1248179697561370655): MPI SF rendía mal; propone partir 1–2 ply y resolver cada subárbol en otra máquina | OpenBench reparte claims gruesos y autónomos |
| 2024-06-06 | ubdip `1248180040638922866` | [analysis:1777](https://discord.com/channels/779317816897699850/812407482369441813/1248180040638922866): Antichess se atacó por subárboles y se ensambló después | Proof DAG componible y verificación de dependencias |
| 2024-06-06 | ubdip `1248232765540008066` | [analysis:1784](https://discord.com/channels/779317816897699850/812407482369441813/1248232765540008066): shallow distributed search es difícil; deep search desde splits superiores es naturalmente distribuible | Nada de TT global WAN ni mensajes por nodo |
| 2024-06-06 | ubdip `1248233853378760709` | [analysis:1786](https://discord.com/channels/779317816897699850/812407482369441813/1248233853378760709): alpha-beta solo como oracle; PNS para automatizar el solve | Confirma la arquitectura de cinco piezas del roadmap |
| 2024-06-06 | dpldgr `1248234583200108625` | [analysis:1787](https://discord.com/channels/779317816897699850/812407482369441813/1248234583200108625): propone TDS | Backend de clúster homogéneo a benchmarkear, no protocolo WAN |
| 2025-09-22 | ubdip `1419719405935530146` | [nnue-training:5079](https://discord.com/channels/779317816897699850/966610323987660830/1419719405935530146): cluster branch paraleliza una sola búsqueda y puede servir para solving | OpenBench WAN y cluster interno son capas complementarias |

### 2.2 La dificultad real está en cobertura defensiva, fortalezas y ramas tardías

| Fecha UTC | Autor / message ID | Evidencia exacta | Uso en el proyecto |
|---|---|---|---|
| 2021-03-15 | ijhy `821148061454696498` | [analysis:213](https://discord.com/channels/779317816897699850/812407482369441813/821148061454696498): Antichess suele tener 1–3 jugadas; Atomic conserva muchas sidelines | No extrapolar coste ni compresión de Antichess |
| 2021-03-15 | mtaktikos `821147959730503680` | [analysis:212](https://discord.com/channels/779317816897699850/812407482369441813/821147959730503680): interesa la vía más segura de certificar, no la más rápida | Oracle optimiza coste del certificado, no mate mínimo |
| 2021-03-15 | mtaktikos `821137971121487922` | [analysis:193](https://discord.com/channels/779317816897699850/812407482369441813/821137971121487922): propone probar primero una regla de 20 movimientos y heredar wins a 50 | Benchmark útil, pero la implicación debe formalizarse para target, claim policy e historial antes de reutilizar facts |
| 2021-03-15 | ubdip `821139366024839188` | [analysis:196](https://discord.com/channels/779317816897699850/812407482369441813/821139366024839188): advertía que el solve parecía poco realista y quizá solo se llegase a un final humanamente claro | Un final «obvio» para humanos sigue necesitando TB/certificate para cerrar |
| 2021-04-04 | deleted/gannet `828155766950068244` | [analysis:228](https://discord.com/channels/779317816897699850/812407482369441813/828155766950068244): hasta `1...e5`, creída perdida, necesita prueba completa | Ningún opening book autoriza omitir una defensa |
| 2021-04-04 | deleted/gannet `828155909749604352` | [analysis:229](https://discord.com/channels/779317816897699850/812407482369441813/828155909749604352): seeds tras `3.g4`: `4...e5`, `4...d6`, `5...g6`, `5...Bd6`, `5...Bb4` | Prioridad inicial de claims, no whitelist |
| 2021-04-04 | deleted/gannet `828156028532162600` | [analysis:230](https://discord.com/channels/779317816897699850/812407482369441813/828156028532162600): seeds posteriores `6...Bd6`, `7...fxe4/e5`, `8...h6/Nd7`, `11...Na6`, `12...Na6` | Corpus de ramas defensivas históricas |
| 2021-04-04 | deleted/gannet `828241989203001385` | [analysis:241](https://discord.com/channels/779317816897699850/812407482369441813/828241989203001385): todas las ramas `...Nh6` daban >+5 a d30, pero una weak proof podía estar a décadas | Un score alto no estima el proof frontier |
| 2021-04-05 | deleted/gannet `828685113981534228` | [analysis:244](https://discord.com/channels/779317816897699850/812407482369441813/828685113981534228): `...c6` tiene muchas más ramas que `...Nh6` | Censo por `...Nh6` antes de `...c6` |
| 2024-04-22 | ubdip `1232073153480622211` | [development:2820](https://discord.com/channels/779317816897699850/779319972614242354/1232073153480622211): forks de mate/study retiraban podas problemáticas como NMP | Crear y medir perfiles de oracle específicos de solving |

### 2.3 El intento de aiorla es valioso precisamente porque no cerró

Thread: `1260924156526985307`, “Atomic NNUE next gen coordination”.

| Fecha UTC | Autor / message ID | Evidencia exacta | Lectura correcta |
|---|---|---|---|
| 2024-07-11 | aiorla `1260945389259849758` | [thread:34](https://discord.com/channels/779317816897699850/1260924156526985307/1260945389259849758): >100k posiciones a depth 35 y +930 | Corpus de búsqueda, no árbol de prueba |
| 2024-07-11 | aiorla `1260952273060626462` | [thread:36](https://discord.com/channels/779317816897699850/1260924156526985307/1260952273060626462): Python escogía la menor eval y analizaba una blanca contra todas las negras | Precursor de un frontier manager, sin exactitud ni certificados |
| 2024-07-11 | aiorla `1260958073351307354` | [thread:41](https://discord.com/channels/779317816897699850/1260924156526985307/1260958073351307354): posiciones +1000 sin progreso claro | Fixtures para oracle/progress heuristics |
| 2024-07-11 | aiorla `1261022480680616037` | [thread:45](https://discord.com/channels/779317816897699850/1260924156526985307/1261022480680616037): posible que algunas sean fortalezas no ganables | El complemento requiere pruebas de seguridad/ciclos, no cp |
| 2024-07-11 | lesha2002 `1261025565222375474` | [thread:50](https://discord.com/channels/779317816897699850/1260924156526985307/1261025565222375474): demasiadas sidelines incluso en mainlines malas | Medir pendiente neta del frontier |
| 2024-07-11 | lesha2002 `1261035971915415735` | [thread:57](https://discord.com/channels/779317816897699850/1260924156526985307/1261035971915415735): `Nf3-e3` alcanza finales que Fairy vs Fairy convierte mal | No priorizar esa ruta solo por score histórico |
| 2024-07-11 | aiorla `1261038217612759122` | [thread:58](https://discord.com/channels/779317816897699850/1260924156526985307/1261038217612759122): FEN adversarial `2r1B3/r1P5/8/3pp1pp/1B1PP1PP/8/7K/2N2bkR w - - 19 1`; el mensaje original omite el fullmove y el `1` es la normalización documentada | Golden fixture para fortaleza/eval/TB |

El script, árbol y logs prometidos no aparecen entre los adjuntos del vault. Deben solicitarse a aiorla y preservarse con licencia/procedencia/checksum; no se deben reconstruir silenciosamente como si fueran el original.

Hay además un antecedente público de 2015: una respuesta en [Chess Stack Exchange](https://chess.stackexchange.com/questions/9941/how-to-solve-atomic-chess) propuso exactamente un árbol con una jugada blanca, todas las respuestas negras y expansión repetida de la hoja de menor evaluación, para pasar después a mates/TB. También advertía que el árbol podía no converger. El script de aiorla materializó prácticamente esa propuesta nueve años después; sus fortalezas y pozos son el experimento que faltaba y justifican pasar de «mínimo cp» a PNS/certificados.

### 2.4 Syzygy Atomic sí ayuda, pero exige semántica propia

| Fecha UTC | Autor / message ID | Evidencia exacta | Consecuencia |
|---|---|---|---|
| 2021-11-15 | ubdip `909859570552279082` | [general:3425](https://discord.com/channels/779317816897699850/779317816897699854/909859570552279082): el prober Atomic adapta touching kings | Fixture obligatorio y prober independiente, no Syzygy orthodox copy-paste |
| 2023-12-24 | ubdip `1188576876676005898` | [general:7471](https://discord.com/channels/779317816897699850/779317816897699854/1188576876676005898): `syzygy1/tb` genera Atomic hasta 6 piezas | Inventario 3–6-men como primera capa exacta candidata |
| 2023-12-24 | ubdip `1188579786935763055` | [general:7476](https://discord.com/channels/779317816897699850/779317816897699854/1188579786935763055): probing dentro del árbol ayuda mucho más | Integrar prober en solver/oracle |
| 2023-12-24 | ubdip `1188601494501609612` | [general:7487](https://discord.com/channels/779317816897699850/779317816897699854/1188601494501609612): 5/6-men + MV-SF no resolvieron Atomic ni Antichess | No confundir disponibilidad TB con viabilidad del solve |

WDL/DTZ no incorporan por sí solos historial de repetición, derechos de enroque ni toda la política rule50. Una leaf solo puede cerrar tras `tb_leaf_applicability` o queda explícitamente condicionada a un manifest/perfil de confianza.

### 2.5 Dos precedentes negativos que cambian gates

1. **Atomic-crazyhouse:** ubdip dijo primero que estaba «básicamente resuelto» ([general:122](https://discord.com/channels/779317816897699850/779317816897699854/782349069372096582), message `782349069372096582`, 2020-11-28), pero explicó que no lo incluyó en la wiki porque el código no reflejaba todas las reglas y el double-check fue manual ([general:129](https://discord.com/channels/779317816897699850/779317816897699854/782352310314860544), `782352310314860544`). El lesson learned es publicar “analysis/practical solve”, no “weakly solved”, hasta tener certificado reproducible.
2. **6×6 histórico:** el borrador inicial heredó de `atomic` ([help:793](https://discord.com/channels/779317816897699850/791247944463417374/816735608231821352), `816735608231821352`), pero el snippet posterior se encabezó `[6x6atomic:nocheckatomic]`, con `extinctionPseudoRoyal` y sin double-step ([help:817](https://discord.com/channels/779317816897699850/791247944463417374/816738779313733694), `816738779313733694`). Ese encabezado histórico no es el token vigente: Fairy-Stockfish expone `6x6atom` y lo declara como [`[6x6atom:nocheckatomic]`](https://github.com/fairy-stockfish/Fairy-Stockfish/blob/fb78cb561aa01708338e35b3dc3b65a42149a3c4/src/variants.ini#L712). Toda campaña lab debe usar `6x6atom`; el piloto normativo debe usar Atomic 8×8 exacto.

## 3. Artefactos localizados o recuperables

| Artefacto | Ubicación/evidencia | Estado y acción |
|---|---|---|
| `top100_2200_atomic_11plies.pgn`, `top300_2100_atomic_14plies.pgn` | [thread:86](https://discord.com/channels/779317816897699850/1260924156526985307/1414231825374511215) | Solo metadata; recuperar blobs de stoiksismic |
| `atomic.epd`, libros 477/666, `acb.bin` | [thread:89](https://discord.com/channels/779317816897699850/1260924156526985307/1414448796783607830) | Solo metadata; recuperar de ijhy |
| `atomic960_depth30_.pdf` | [general:1346](https://discord.com/channels/779317816897699850/779317816897699854/800946306209546260) | Solo metadata, 1.117.871 bytes; consultar a bianca/humanwaste |
| `AtomicAntiHill960.pdf` | [analysis:53](https://discord.com/channels/779317816897699850/812407482369441813/815323198496440371) | Solo metadata, 935.182 bytes |
| Mega tournament y v2.1 selfplay | [Mega tournament](https://discord.com/channels/779317816897699850/791249497090686987/818580012122505236), [v2.1 selfplay](https://discord.com/channels/779317816897699850/791249497090686987/872034529555148801) | Solo metadata; recuperar blobs, útiles para openings, no prueba |
| `Atomic_Rating16.pgn` | [metadata Discord](https://discord.com/channels/779317816897699850/791249497090686987/882882082248986665); copia de trabajo recuperada | Recuperado: 1.793.098 bytes, SHA-256 `C9C60808C83528411C446E55F5C8228C55DE85DB411F4D95E364245D2ECB1D1D`; tres copias locales byte-idénticas |
| `atomic.epd` recuperado | copia de trabajo recuperada | 394.785 bytes; SHA-256 `28ED51C2F42E723D5E127D2D3F21C0BFA4A9B318615AFDB299B93EA62DEA2B1E`; distinto del adjunto de 22.099 bytes |
| Net 6×6 local | copia de trabajo recuperada | SHA-256 `8A326F2FBA0310B945E1940A577F9E87074D427790F7986767B87E144D45F16B`; solo `nocheckatomic-6x6-lab` |
| Proof Los Alamos | [figshare DOI 10.6084/m9.figshare.25424674](https://figshare.com/articles/dataset/game2_d2d3_proof_gz/25424674) | Público: 267.410.116 líneas; corpus inmediato de streaming/chunking |
| Proofs y source Antichess | [Watkins](https://magma.maths.usyd.edu.au/~watkins/LOSING_CHESS/) | Públicos; incluyen historial de corrección double-EP y checksums |

No se deben conservar como identificador las URLs firmadas de Discord: caducan. El manifest estable usa filename, author/message ID/date, tamaño, licencia si se obtiene y SHA-256 del blob recuperado.

## 4. Contraste con literatura primaria

1. **Antichess:** Watkins usó PN2/PNS y subsearches gruesas; la prueba actual y source revisados son públicos. El bug double-en-passant demuestra que solver y TB compartidos no bastan ([proyecto](https://magma.maths.usyd.edu.au/~watkins/LOSING_CHESS/), [revisión](https://magma.maths.usyd.edu.au/~watkins/LOSING_CHESS/revision.html)).
2. **Los Alamos Chess:** Fairy-Stockfish fue oracle, el proof se dividió en ~200 subtareas y el artefacto final se verifica aparte. El paper señala que I/O del árbol es mucho más lento que el search y publica 12 GB raw / 1,5 GB gzip ([paper, DOI 10.3233/ICG-240247](https://doi.org/10.3233/ICG-240247)).
3. **GHI:** la misma posición puede tener valor/legalidad diferente según el camino; el historial no puede reducirse a Zobrist o contador informal ([Kishimoto–Müller](https://webdocs.cs.ualberta.ca/~mmueller/ps/kishimoto-mueller-infsci-ghi.pdf)).
4. **DFPN con repeticiones:** ignorar ciclos puede causar loops y proof/disproof numbers subestimados o sobreestimados; TCA/SNDA son referencias para el backend, no licencia para omitir historial ([Kishimoto 2010](https://ojs.aaai.org/index.php/AAAI/article/view/7534)).
5. **MOPNS:** comparte un árbol para múltiples outcomes y en sus benchmarks creó menos nodos, pero fue algo más lento que PNS acumulado. Debe compararse después del target binario v1, no reemplazarlo por promesa ([paper](https://www.lamsade.dauphine.fr/~cazenave/papers/mopns.pdf)).
6. **PNS-PDFPN/TDS:** prometen escalado en clústeres homogéneos y redes rápidas; no justifican un TT distribuido sobre workers voluntarios WAN ([PNS-PDFPN](https://ojs.aaai.org/index.php/AAAI/article/download/41010/44971), [TDS](https://webdocs.cs.ualberta.ca/~jonathan/publications/parrallel_computing_publications/tt.pdf)).
7. **Threat-sequence search:** la literatura identifica las amenazas de muerte súbita como reductoras del proof tree. Atomic es candidato natural por explosiones y jaques, pero solo la búsqueda exhaustiva de respuestas puede producir un subproof; threat-space heurístico sigue siendo incompleto y lambda search no se asume válida sin pass/ausencia de zugzwang ([Heule–Rothkrantz](https://www.cs.cmu.edu/~mheule/publications/solving_games.pdf)).
8. **QBF de horizonte acotado:** puede codificar «existe estrategia ganadora en ≤N ply», extraer una estrategia y validarla; es un cross-check independiente excelente para roots pequeñas, no un sustituto escalable de DFPN ni solución para horizonte desconocido/ciclos ([Shaik–van de Pol](https://arxiv.org/abs/2303.16949), [QBFcert](https://fmv.jku.at/qbfcert/)).

## 5. Correcciones técnicas incorporadas al roadmap

### 5.1 Piloto

- Eliminado `6x6atom` como gate normativo.
- S2 usa mates/roots pequeñas de Atomic 8×8 exacto, fuera y dentro de TB.
- `nocheckatomic-6x6-lab` queda opcional y aislado por rules/schema/namespace/dashboard.

### 5.2 Target frente a complemento

- `PROVED_TARGET` es alcanzabilidad y exige DAG o rank estrictamente decreciente.
- `PROVED_COMPLEMENT` cambia los operadores: universal cuando mueve el target; existencial cuando mueve el oponente.
- Un ciclo no prueba tablas por reaparecer el hash. Fortalezas/repeticiones requieren `SafetyCertificate` coinductivo: conjunto finito history-aware, cierre, SCCs, estrategia testigo y semántica de infinite play congelada.
- Hasta verificar ese formato, complementos cíclicos permanecen `UNKNOWN`.
- Como un SCC no puede usar hashes recursivos de nodo, se serializa canónicamente como unidad con índices locales; el DAG de condensación sí es content-addressed.

### 5.3 Identidades y tablebases

- `claim_id` identifica el teorema semántico: reglas, adjudicación, estado, historial y target.
- `profile_id` identifica verifier policy, TB manifest y trust mode.
- `fact_id` enlaza claim, resultado, certificate y profile.
- Una prueba condicional a TB no se reutiliza como incondicional; una prueba TB-free puede promover el mismo claim sin duplicar el grafo semántico.
- La compatibilidad es un predicado explícito y fail-closed: `TB_FREE` puede satisfacer perfiles más permisivos; `TB_CONDITIONAL` no puede cerrar una campaña incondicional; manifests/rules incompatibles nunca se mezclan.

### 5.4 OpenBench Proof mínimo viable

Modelos:

```text
ProofCampaign
ProofClaim
ProofProfile
ProofExpansion / ProofDependency
ProofWorkUnit
ProofAttempt / ProofLease
ProofArtifact
ProofVerification
AcceptedFact
```

State machine:

```text
QUEUED -> LEASED -> CANDIDATE -> VERIFYING -> ACCEPTED
                    |               |
                    |               +-> REJECTED
                    +-> UNKNOWN/CHECKPOINT -> QUEUED
LEASED --timeout--> ORPHANED -> QUEUED
```

Invariantes:

- Artifact upload en dos fases: temp object, hash/size/decompression validation, transaction commit.
- `ProofExpansion` enumera todas las jugadas legales y entra en el grafo superior solo tras regenerar movegen, child state/history, operador y cobertura; el certificate puede compactar el witness existencial después.
- Unknown children reciben pn/dn inicial; únicamente `AcceptedFact` aporta cierres exactos `0/INF`.
- Retry crea `attempt_id` nuevo sobre el mismo `work_id`; late results pueden verificarse, pero no mutan frontier sin compare-and-swap.
- Verificación corre en una queue/sandbox separados.
- Logs/checkpoints grandes viven en object storage; no en JSON/stdout ni en PostgreSQL.
- Restauración de backup debe reconstruir frontier y todos los facts solo desde manifests/chunks.

## 6. Próxima ejecución recomendada

Orden inmediato:

1. ADR de reglas, draw claims e infinite-play semantics.
2. Schema/hash vectors cross-language.
3. Manifest de arqueología y recuperación de aiorla/books/PDF/PGN.
4. Verificador externo + corpus de reglas.
5. Harness Los Alamos/Antichess para streaming y corrupción.
6. PNS de referencia, DFPN monohilo y certificados DAG.
7. `SafetyCertificate` en juegos sintéticos antes de fortalezas Atomic.
8. Threat-sequence/mate subsolver exacto como generador de subproofs verificados.
9. QBF bounded-depth independiente sobre las mismas roots diminutas.
10. Prober 3–6-men independiente con touching-kings fixtures.
11. Modelos/API `PROOF` detrás de feature flag en OpenBench.
12. Tres workers locales, roots 8×8 conocidas, después piloto 10–50 workers y censo `...Nh6` → `...c6`.

El resultado de este deep dive no cambia la meta, pero endurece tres puntos críticos: no usar 6×6 como falsa equivalencia, no aceptar ciclos como tablas y no confundir un manifest TB concreto con la identidad semántica del claim.

## Apéndice A: índice completo de citas Discord

Cada identificador enlaza directamente al mensaje. Autor y fecha UTC proceden de la exportación deduplicada; Referencia indica si la cita aparece en el roadmap, en este ledger o en ambos.

| Fecha UTC | Autor | Message ID | Referencia |
|---|---|---|---|
| 2020-11-28 | `ubdip` | [`782349069372096582`](https://discord.com/channels/779317816897699850/779317816897699854/782349069372096582) | roadmap — mensaje; ledger — general:122 |
| 2020-11-28 | `ubdip` | [`782352310314860544`](https://discord.com/channels/779317816897699850/779317816897699854/782352310314860544) | roadmap — matiz decisivo; ledger — general:129 |
| 2020-12-22 | `baera1733` | [`790904512407011348`](https://discord.com/channels/779317816897699850/779317816897699854/790904512407011348) | roadmap — Discord: FEN alcanzable |
| 2020-12-27 | `ubdip` | [`792903316614545438`](https://discord.com/channels/779317816897699850/779319972614242354/792903316614545438) | roadmap — Discord: divergencia de reglas |
| 2021-01-15 | `ubdip` | [`799645341489430568`](https://discord.com/channels/779317816897699850/779319972614242354/799645341489430568) | roadmap — Discord: rama sin jaque |
| 2021-01-19 | `bianca5` | [`800946306209546260`](https://discord.com/channels/779317816897699850/779317816897699854/800946306209546260) | ledger — general:1346 |
| 2021-01-24 | `belzedar_` | [`802962819049717800`](https://discord.com/channels/779317816897699850/779317816897699854/802962819049717800) | ledger — antecedente de máquina solver estilo fishtest |
| 2021-01-24 | `belzedar_` | [`802962902884810752`](https://discord.com/channels/779317816897699850/779317816897699854/802962902884810752) | ledger — continuación sobre variantes resolubles |
| 2021-01-26 | `ubdip` | [`803658203891630150`](https://discord.com/channels/779317816897699850/784418118503235625/803658203891630150) | ledger — nnue-general:338 |
| 2021-02-27 | `Deleted User` | [`815323198496440371`](https://discord.com/channels/779317816897699850/812407482369441813/815323198496440371) | ledger — analysis:53 |
| 2021-03-03 | `belzedar_` | [`816735608231821352`](https://discord.com/channels/779317816897699850/791247944463417374/816735608231821352) | roadmap — mensaje; ledger — help:793 |
| 2021-03-03 | `belzedar_` | [`816738779313733694`](https://discord.com/channels/779317816897699850/791247944463417374/816738779313733694) | roadmap — configuración del hilo; ledger — help:817 |
| 2021-03-07 | `ijhy` | [`818228191319556126`](https://discord.com/channels/779317816897699850/791249497090686987/818228191319556126) | roadmap — discusión |
| 2021-03-07 | `ijhy` | [`818228245291860018`](https://discord.com/channels/779317816897699850/791249497090686987/818228245291860018) | roadmap — matiz |
| 2021-03-07 | `belzedar_` | [`818230283455168523`](https://discord.com/channels/779317816897699850/791249497090686987/818230283455168523) | roadmap — Discord: servidores |
| 2021-03-08 | `ubdip` | [`818442571582930979`](https://discord.com/channels/779317816897699850/812407482369441813/818442571582930979) | roadmap — Discord 2021: oráculo; ledger — analysis:94 |
| 2021-03-08 | `ubdip` | [`818443628808372225`](https://discord.com/channels/779317816897699850/812407482369441813/818443628808372225) | roadmap — Discord 2021: PNS; ledger — analysis:99 |
| 2021-03-08 | `ubdip` | [`818447247464464394`](https://discord.com/channels/779317816897699850/812407482369441813/818447247464464394) | ledger — analysis:101 |
| 2021-03-08 | `Deleted User` | [`818580012122505236`](https://discord.com/channels/779317816897699850/791249497090686987/818580012122505236) | ledger — Mega tournament |
| 2021-03-15 | `Deleted User` | [`821083552073908245`](https://discord.com/channels/779317816897699850/812407482369441813/821083552073908245) | roadmap — Discord: alternativas |
| 2021-03-15 | `Deleted User` | [`821084418436628491`](https://discord.com/channels/779317816897699850/812407482369441813/821084418436628491) | roadmap — Discord: defensas tardías |
| 2021-03-15 | `Deleted User` | [`821102431571148821`](https://discord.com/channels/779317816897699850/812407482369441813/821102431571148821) | roadmap — pawnitization 1 |
| 2021-03-15 | `Deleted User` | [`821112401410654270`](https://discord.com/channels/779317816897699850/812407482369441813/821112401410654270) | roadmap — pawnitization 2 |
| 2021-03-15 | `mtaktikos` | [`821137971121487922`](https://discord.com/channels/779317816897699850/812407482369441813/821137971121487922) | ledger — analysis:193 |
| 2021-03-15 | `ubdip` | [`821139366024839188`](https://discord.com/channels/779317816897699850/812407482369441813/821139366024839188) | ledger — analysis:196 |
| 2021-03-15 | `mtaktikos` | [`821147959730503680`](https://discord.com/channels/779317816897699850/812407482369441813/821147959730503680) | roadmap — mensaje; ledger — analysis:212 |
| 2021-03-15 | `ijhy` | [`821148061454696498`](https://discord.com/channels/779317816897699850/812407482369441813/821148061454696498) | roadmap — discusión; ledger — analysis:213 |
| 2021-04-04 | `Deleted User` | [`828155766950068244`](https://discord.com/channels/779317816897699850/812407482369441813/828155766950068244) | roadmap — mensaje; ledger — analysis:228 |
| 2021-04-04 | `Deleted User` | [`828155909749604352`](https://discord.com/channels/779317816897699850/812407482369441813/828155909749604352) | roadmap — primer grupo; ledger — analysis:229 |
| 2021-04-04 | `Deleted User` | [`828156028532162600`](https://discord.com/channels/779317816897699850/812407482369441813/828156028532162600) | roadmap — segundo grupo; ledger — analysis:230 |
| 2021-04-04 | `Deleted User` | [`828157064018395177`](https://discord.com/channels/779317816897699850/812407482369441813/828157064018395177) | roadmap — mensaje |
| 2021-04-04 | `Deleted User` | [`828241989203001385`](https://discord.com/channels/779317816897699850/812407482369441813/828241989203001385) | roadmap — Discord: comparación de defensas; ledger — analysis:241 |
| 2021-04-05 | `Deleted User` | [`828685113981534228`](https://discord.com/channels/779317816897699850/812407482369441813/828685113981534228) | ledger — analysis:244 |
| 2021-07-22 | `ubdip` | [`867659752875491388`](https://discord.com/channels/779317816897699850/793813826436464640/867659752875491388) | roadmap — mensaje; ledger — off-topic:151 |
| 2021-08-03 | `ijhy` | [`872034529555148801`](https://discord.com/channels/779317816897699850/791249497090686987/872034529555148801) | ledger — v2.1 selfplay |
| 2021-08-26 | `belzedar_` | [`880354111046942730`](https://discord.com/channels/779317816897699850/812407482369441813/880354111046942730) | roadmap — Discord: ramificación c6 |
| 2021-09-02 | `belzedar_` | [`882882082248986665`](https://discord.com/channels/779317816897699850/791249497090686987/882882082248986665) | roadmap — el adjunto histórico; ledger — metadata Discord |
| 2021-11-15 | `ubdip` | [`909859570552279082`](https://discord.com/channels/779317816897699850/779317816897699854/909859570552279082) | roadmap — mensaje; ledger — general:3425 |
| 2022-06-13 | `ubdip` | [`985992353372307516`](https://discord.com/channels/779317816897699850/779317816897699854/985992353372307516) | roadmap — Discord: tres niveles; ledger — general:5468 |
| 2023-02-17 | `occyroexanthub` | [`1076262003099828325`](https://discord.com/channels/779317816897699850/812407482369441813/1076262003099828325) | roadmap — regresión NNUE/tablebase |
| 2023-03-29 | `ubdip` | [`1090559239740719144`](https://discord.com/channels/779317816897699850/793813826436464640/1090559239740719144) | roadmap — Discord: verificación independiente |
| 2023-04-02 | `ubdip` | [`1092084211797721108`](https://discord.com/channels/779317816897699850/779319972614242354/1092084211797721108) | roadmap — Discord: reyes adyacentes |
| 2023-06-08 | `ubdip` | [`1116257675894857799`](https://discord.com/channels/779317816897699850/966610323987660830/1116257675894857799) | roadmap — Discord: presión de RAM |
| 2023-12-24 | `ubdip` | [`1188576876676005898`](https://discord.com/channels/779317816897699850/779317816897699854/1188576876676005898) | roadmap — soporte 6-men; ledger — general:7471 |
| 2023-12-24 | `ubdip` | [`1188579786935763055`](https://discord.com/channels/779317816897699850/779317816897699854/1188579786935763055) | roadmap — probing interno; ledger — general:7476 |
| 2023-12-24 | `ubdip` | [`1188601494501609612`](https://discord.com/channels/779317816897699850/779317816897699854/1188601494501609612) | roadmap — mensaje; ledger — general:7487 |
| 2024-04-22 | `ubdip` | [`1232073153480622211`](https://discord.com/channels/779317816897699850/779319972614242354/1232073153480622211) | roadmap — mensaje; ledger — development:2820 |
| 2024-06-06 | `ubdip` | [`1248179697561370655`](https://discord.com/channels/779317816897699850/812407482369441813/1248179697561370655) | roadmap — mensaje; ledger — analysis:1776 |
| 2024-06-06 | `ubdip` | [`1248180040638922866`](https://discord.com/channels/779317816897699850/812407482369441813/1248180040638922866) | roadmap — mensaje; ledger — analysis:1777 |
| 2024-06-06 | `ubdip` | [`1248232765540008066`](https://discord.com/channels/779317816897699850/812407482369441813/1248232765540008066) | roadmap — mensaje; ledger — analysis:1784 |
| 2024-06-06 | `ubdip` | [`1248233853378760709`](https://discord.com/channels/779317816897699850/812407482369441813/1248233853378760709) | roadmap — mensaje; ledger — analysis:1786 |
| 2024-06-06 | `dpldgr` | [`1248234583200108625`](https://discord.com/channels/779317816897699850/812407482369441813/1248234583200108625) | roadmap — mensaje; ledger — analysis:1787 |
| 2024-07-07 | `aiorla` | [`1259607759968796764`](https://discord.com/channels/779317816897699850/812407482369441813/1259607759968796764) | roadmap — caso |
| 2024-07-07 | `aiorla` | [`1259620577098993665`](https://discord.com/channels/779317816897699850/812407482369441813/1259620577098993665) | roadmap — diagnóstico |
| 2024-07-11 | `aiorla` | [`1260945389259849758`](https://discord.com/channels/779317816897699850/1260924156526985307/1260945389259849758) | roadmap — mensaje; ledger — thread:34 |
| 2024-07-11 | `aiorla` | [`1260952273060626462`](https://discord.com/channels/779317816897699850/1260924156526985307/1260952273060626462) | roadmap — método; ledger — thread:36 |
| 2024-07-11 | `aiorla` | [`1260958073351307354`](https://discord.com/channels/779317816897699850/1260924156526985307/1260958073351307354) | roadmap — pozos; ledger — thread:41 |
| 2024-07-11 | `aiorla` | [`1261022480680616037`](https://discord.com/channels/779317816897699850/1260924156526985307/1261022480680616037) | roadmap — fortalezas; ledger — thread:45 |
| 2024-07-11 | `lesha2002` | [`1261025565222375474`](https://discord.com/channels/779317816897699850/1260924156526985307/1261025565222375474) | roadmap — sidelines; ledger — thread:50 |
| 2024-07-11 | `lesha2002` | [`1261035971915415735`](https://discord.com/channels/779317816897699850/1260924156526985307/1261035971915415735) | roadmap — Discord: endgames difíciles; ledger — thread:57 |
| 2024-07-11 | `aiorla` | [`1261038217612759122`](https://discord.com/channels/779317816897699850/1260924156526985307/1261038217612759122) | roadmap — posición difícil de aiorla; ledger — thread:58 |
| 2025-07-27 | `ijhy` | [`1398851298438414448`](https://discord.com/channels/779317816897699850/779317816897699854/1398851298438414448) | roadmap — Discord: limitaciones de atomiktest |
| 2025-09-07 | `stoiksismic` | [`1414231825374511215`](https://discord.com/channels/779317816897699850/1260924156526985307/1414231825374511215) | roadmap — adjuntos; ledger — thread:86 |
| 2025-09-08 | `ijhy` | [`1414448796783607830`](https://discord.com/channels/779317816897699850/1260924156526985307/1414448796783607830) | roadmap — adjuntos; ledger — thread:89 |
| 2025-09-22 | `ubdip` | [`1419719405935530146`](https://discord.com/channels/779317816897699850/966610323987660830/1419719405935530146) | roadmap — mensaje; ledger — nnue-training:5079 |
| 2026-01-17 | `ubdip` | [`1462051281387524128`](https://discord.com/channels/779317816897699850/779319972614242354/1462051281387524128) | roadmap — Discord: worker pull |
| 2026-01-17 | `ubdip` | [`1462052988838219874`](https://discord.com/channels/779317816897699850/779319972614242354/1462052988838219874) | roadmap — Discord: OpenBench moderno |
