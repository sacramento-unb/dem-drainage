# Extração de drenagem DEM com WhiteboxTools

Esta ferramenta de linha de comando recebe uma pasta com arquivos DEM, processa cada arquivo separadamente com o [WhiteboxTools](https://github.com/jblindsay/whitebox-tools) e calcula a **ordem de Hack**:

- `1`: drenagem principal;
- `2`: tributários que entram na drenagem principal;
- `3+`: níveis sucessivos de tributários a montante.

O GeoParquet final armazena essa hierarquia em `hack_order` e é gravado diretamente a partir do raster ordenado. Esta é a ordem de Hack, não a ordem de Strahler: Strahler atribui `1` às cabeceiras, o inverso da convenção usada aqui.

## Pipeline

Para cada arquivo, o processamento:

1. Valida o DEM de banda única e seu SRC.
2. Executa `BreachDepressions` para corrigir barreiras artificiais e, por padrão, preencher depressões de até uma célula.
3. Cria os rasters de direção e acumulação de fluxo D8.
4. Extrai a rede de drenagem usando um limiar configurável de área contribuinte.
5. Opcionalmente remove tributários curtos.
6. Executa `HackStreamOrder` e grava um GeoParquet para o arquivo processado.

O uso de D8 é intencional: o WhiteboxTools exige um ponteiro D8 para `HackStreamOrder`. O MD-infinity é útil para o fluxo distribuído em encostas, mas não define a única direção a jusante necessária para ordenar hierarquicamente os canais.

## Instalação

Compile ou baixe o executável do repositório original:

```bash
git clone https://github.com/jblindsay/whitebox-tools.git
cd whitebox-tools
python build.py
```

Depois instale este projeto:

```bash
python -m pip install -e .
```

Informe o executável `whitebox_tools` com `--whitebox-exe`, coloque-o no `PATH` ou defina `WHITEBOX_TOOLS_EXE`.

## Execução

A execução mínima é:

```bash
dem-drainage /data/dem_tiles \
  --whitebox-exe /opt/whitebox-tools/WBT/whitebox_tools
```

## Execução com Docker (recomendado)

É possível executar a API de processamento e a aplicação web React com Docker Compose:

```bash
docker compose up -d
```

- Interface web: [http://localhost:3055](http://localhost:3055)
- Documentação e saúde da API: [http://localhost:8055/docs](http://localhost:8055/docs)

Para executar a CLI via Docker em uma pasta local de DEMs:

```bash
docker compose run --rm cli /data/my_dems --output-dir /data/results
```

## Interface web local (sem Docker)

A interface React permite selecionar os arquivos DEM, enviá-los ao processador local, acompanhar o andamento, visualizar os resultados no mapa e gerenciar os arquivos gerados.

Inicie a API de processamento em um terminal:

```bash
export WHITEBOX_TOOLS_EXE=/opt/whitebox-tools/WBT/whitebox_tools
.venv/bin/dem-drainage-web
```

Inicie a interface React em outro terminal:

```bash
cd web
npm install
npm run dev
```

Abra `http://localhost:3055`. Os processamentos enviados ficam, por padrão, no diretório temporário do sistema. Defina `DEM_DRAINAGE_JOB_DIR` para escolher um diretório persistente.

O limiar padrão para iniciar os canais é de 10.000 células. Ajuste esse valor com base em canais conhecidos ou imagens de referência; não há um valor universal. Por exemplo, em um DEM com resolução de 10 cm, uma área de contribuição de 1.000 m² equivale a 100.000 células:

```bash
dem-drainage /data/dem_tiles \
  --threshold-cells 100000 \
  --min-stream-length 5 \
  --output-dir /data/results \
  --whitebox-exe /opt/whitebox-tools/WBT/whitebox_tools
```

Em um SRC projetado, o limiar também pode ser expresso em unidades quadradas do mapa:

```bash
dem-drainage /data/dem_tiles --threshold-area 1000
```

Opções operacionais úteis:

- `--recursive`: procura DEMs em subpastas;
- `--pattern '*.tif'`: escolhe padrões glob para os nomes dos arquivos, podendo ser repetido;
- `--resume`: reutiliza saídas de etapas já criadas;
- `--no-fill-remaining`: não tenta corrigir depressões que excedam os limites definidos;
- `--clean`: remove rasters intermediários após uma execução bem-sucedida;
- `--dry-run`: exibe os comandos exatos do WhiteboxTools.

## Saídas

A pasta de saída contém um conjunto de arquivos por bloco de entrada. Para `raster_0_0.tif`, por exemplo:

- `raster_0_0_drainage_hack_order.parquet`: drenagem GeoParquet final com as colunas `hack_order`, `length` e `geometry`;
- `raster_0_0_hack_order.tif`: raster de canais ordenados;
- `raster_0_0_streams.tif` ou `raster_0_0_streams_pruned.tif`: rede binária extraída;
- `raster_0_0_flow_accumulation.tif`, `raster_0_0_d8_pointer.tif` e `raster_0_0_dem_breached.tif`: intermediários auditáveis;
- `raster_0_0_run_manifest.json`: entradas, parâmetros, saídas e significado da hierarquia.

## Escala e ressalva sobre blocos

Os blocos são intencionalmente isolados: a área contribuinte, a extração de canais e a ordem de Hack reiniciam em cada limite de bloco. Isso evita criar um mosaico e mantém cada GeoParquet independente; não produz uma rede de drenagem globalmente contínua entre as bordas dos blocos.

Blocos DEM inteiros sem um valor NoData declarado têm suas células zero tratadas como NoData antes do processamento. Blocos compostos somente por zeros são ignorados por não conterem dados de elevação.

A vetorização direta do GeoParquet atualmente carrega em memória os rasters de canais ordenados e de ponteiro D8. Mantenha cada área de processamento pequena o suficiente para que esses dois rasters e os arrays de trabalho caibam na RAM.

## Testes

```bash
python -m pip install -e '.[test]'
pytest
```

## Audit status and limits

The package metadata names DigiFarmz as the author. Confirm ownership and
permission to present this repository as personal portfolio work before any
publication. The local audit found no dataset fixture and did not run Docker,
WhiteboxTools or the web application.

The API accepts uploaded files without an explicit size quota, keeps job state
in process memory and defaults its CORS origin list to include `*` for local
convenience. Restrict `DEM_DRAINAGE_CORS_ORIGINS`, add authentication and set
resource/time limits before exposing it beyond a trusted local network.

On 2026-10-20, source compilation and `git diff --check` passed. The optional
test command could not run because `pytest` is not installed in the audit
environment; no remote operation was performed.
