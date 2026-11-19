# BioGears' CSV-to-XML data generators (vendored, unmodified)

These 14 files are copied byte for byte from BioGears 8.2.0
(https://github.com/BioGearsEngine/core, tag `8.2.0`, commit
`c5227b63888a7b600087f6aeff7c5649d9b7e8f9`), `projects/cli/src/data/`. They are the
converters behind `bg-cli GENERATE data`: each reads one spreadsheet template
(`templates/Patients.csv`, `Substances.csv`, `Compounds.csv`, `Environments.csv`,
`Nutritions.csv`, `Stabilizations.csv`) and writes BioGears' XML data files.

BioGears installs them only as part of its command-line tool, not as a library, so
pybiogears compiles them into its binding (`src/cpp/generate_data.cpp`) to run them from
Python (`pybiogears.generate_data`). They are not edited: everything pybiogears changes
about how they run (the working directory, capturing their console output) happens
around them. `tests/test_generate_data.py` checks each file's SHA-256 against the list
below, so an edit shows up as a test failure.

They are licensed by Applied Research Associates under the Apache License, Version 2.0,
as stated in each file's header.

| file | SHA-256 |
|---|---|
| `CSVToXMLConvertor.cpp` | `fa8aebe1f853db9c031022ba57994c599380146d89d8c909283548afa89146be` |
| `CSVToXMLConvertor.h` | `60db65e686994374e8c84b95d98ee8c9eda2326806be7b63cc03938c7f24646f` |
| `CompoundGenerator.cpp` | `b2b7e8c461d614237b31fc9e442d06282984669e9abb4e42fb25e8aba9d19ee6` |
| `CompoundGenerator.h` | `cce52a765f3cd447c72c4e93aa39db1028896d91db0bc91a1f081529612792ba` |
| `EnvironmentGenerator.cpp` | `e5354182a6b1a58fb3b5e2e5b2dee5f51f3f195a8a0150da9e0d166451f0894a` |
| `EnvironmentGenerator.h` | `6384d16d438b9e7f704a3ca38de64f445363104887f7a0387378a4f65d18898c` |
| `NutritionGenerator.cpp` | `9aa5f20108f2024d2307ffa302c376cc08e6e9ab298f6de543d7519373ead32d` |
| `NutritionGenerator.h` | `eeb2dff658699e7b0ebb29bc26157c9a980488a46cba8e2d7df0898629533cea` |
| `PatientGenerator.cpp` | `3e44a580bcc15fa6186d57975341d672b3308df370a3c915e3ab3072ab420240` |
| `PatientGenerator.h` | `620dc18c167336ac51fbfb2164d5af6c986e09337538d7e0fb581d1b7705ed44` |
| `StabilizationGenerator.cpp` | `b3147882cd1e8cad48fc0415d0d7c2d43d8e525d4336e46d7cc6db790a466103` |
| `StabilizationGenerator.h` | `75269944718c1eed557a6db29ea5d828bbeb04116e9d4be1c81dd2390838ac9c` |
| `SubstanceGenerator.cpp` | `eb524ba94af03fa544931335effb75dfa9658fc7b3c0f3b4f7122a279169ced6` |
| `SubstanceGenerator.h` | `9f3dda274e57b19c0935cd3071fff00a5c97a40fe3c1d7bdd458409b3992f3d5` |
