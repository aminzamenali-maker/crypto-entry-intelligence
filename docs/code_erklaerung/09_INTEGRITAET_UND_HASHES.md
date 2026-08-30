# 09 - Integritaet und SHA-256-Hashes

## Zweck

Diese Tabelle dokumentiert den Zustand der Python-, SQL- und JSON-Dateien aus der hochgeladenen Projekt-ZIP zum Zeitpunkt der Erstellung dieser Code-Dokumentation.

**Keine dieser Dateien wurde bearbeitet.** Die neue Dokumentation liegt separat unter `docs/code_erklaerung/`. Das Hinzufuegen dieses Dokumentationsordners veraendert die unten aufgefuehrten Dateihashes nicht.

| Datei | SHA-256 | Bytes | Zeilen |
|---|---|---:|---:|
| `src/__init__.py` | `59f5aab13f613b2732f4db772d57b4c4e8ff8277f52cd9b8544c89c29d6a8c12` | 59 | 1 |
| `src/backtest_contract.py` | `4ad4ff9c54a9fd3a74a3212b4b80fac754a4edfef4f8aa3536b75696d426fbc3` | 28,349 | 593 |
| `src/backtest_pipeline.py` | `8a8ab18b5575d5366429683cede5253bb3bd4640964671da8d879848d124d28c` | 52,032 | 868 |
| `src/data_pilot.py` | `57e046f367983e2e46cbcbbbfc9c08f8bbed1fe1f375b5493478f6c852269e1e` | 50,196 | 1,381 |
| `src/eda_powerbi_pipeline.py` | `280f3fd343062dbd725453263682d4b3fcc585c3888e4e9cdb595557c0fc8cba` | 99,068 | 1,818 |
| `src/final_test_once.py` | `189bab4de61f273e61cf62956ca4353ed00970b9b764b625fbfb2150492f845c` | 34,977 | 665 |
| `src/full_import.py` | `ea0f029978ad89a6551b6fbc6de7f15ae41ebe11a27ec4c15024e34342be3ddb` | 139,625 | 3,927 |
| `src/processed_pipeline.py` | `8faae9d11561b12734652a2d0b374affd66009ebf2d6a8b6e4b293bb54f30e6c` | 50,405 | 1,169 |
| `src/sql_pipeline.py` | `2fad7449a74eb6bb24bb657b756a4ada88c913f2f895545170029c1104d49cb5` | 38,168 | 789 |
| `sql/001_schema.sql` | `0be4342d2474f721a705f804c5ce93e9b4e2daf6f8ace4de0b4a89169ef5bacb` | 4,543 | 80 |
| `sql/002_views.sql` | `5d2ac7be9e583a31fe9f1ef160c451291337e209d6049d81f7f29b9ca6ab2466` | 2,355 | 56 |
| `config/backtest.json` | `e4deb6b6ad56a8517f86822d85086524c3cd3c29890d9754453163d7f1107f04` | 27,452 | 773 |
| `config/backtest_phase2b.json` | `16382dc037b56fa30b5cfabff2dc1f336dab81b13c62cffafcef44f5c391a78e` | 2,851 | 78 |
| `config/data_pilot.json` | `a20ab46a39e7e4796a97f740315c3b2110406f0291879dc8678130f528307144` | 6,017 | 145 |
| `config/final_test_once.json` | `7983d955168afed2ad449f6d525880972e18cfdc8170cb497f9de79b528e3a1e` | 3,465 | 70 |
| `config/full_import.json` | `5265aba0c34c3ec836e1d4b94607c962f01f27e007a5335c5a7edbaa92c8172b` | 1,861 | 68 |


## Wichtiger Hinweis zu spaeteren Aenderungen

Wenn spaeter Kommentare direkt in eine `.py`- oder `.sql`-Datei geschrieben werden, aendert sich ihr Hash sofort. Bei JSON wuerde ein Kommentar ausserdem den Standard-JSON-Vertrag verletzen. Deshalb ist die separate Dokumentation die sichere Variante.

## Empfohlene Projektintegration

Den Ordner `docs/code_erklaerung/` einfach zusaetzlich ins Projekt kopieren. Nicht die vorhandenen Dateien unter `src/`, `sql/` oder `config/` ersetzen.
