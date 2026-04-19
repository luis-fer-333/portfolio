# Analytics Engineering Portfolio — Luis Núñez

Source notebooks and code for my analytics engineering portfolio.
Live site: **[luis-fer-333.github.io](https://luis-fer-333.github.io)**

## What's here

13 projects spanning the analytics engineering stack. See the [portfolio website](https://luis-fer-333.github.io/projects) for project writeups with architecture diagrams, key decisions, and trade-offs.

| Area | Projects |
|---|---|
| Data lakes & ETL | Nobel Prize Data Lake, Movie Database ETL, Spark Sentiment Pipeline |
| SQL modeling & BI | Airbnb Valencia BI, Movie Analytics EDA, Formula 1 Data Analysis |
| Time-series | Electricity Demand + InfluxDB |
| ML / MLOps | Customer Analytics, Housing Price ML, Heart Disease MLOps |
| Deep learning | Dog Breed CNN (transfer learning) |
| Recommenders | Book Recommender (hybrid) |
| Cloud architectures | Cinema Serverless AWS |

Each capstone folder contains:
- A portfolio-ready translated notebook (e.g. `Nobel_Prize_ETL_Datalake.ipynb`)
- The original solved notebook (preserved for audit)
- A `data/` folder with small input files (large files are `.gitignored`, see [DATA.md](./DATA.md))
- Companion Python scripts, Dockerfiles, or dashboard exports where applicable

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
jupyter lab
```

## Data

Large datasets (IMDb bulk files, Airbnb listings, etc.) aren't committed to this repo. See [DATA.md](./DATA.md) for per-project download instructions.

## License

MIT.
