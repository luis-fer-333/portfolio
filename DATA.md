# Data Files — Download Instructions

Several projects in this portfolio use data that isn't bundled with the repo because of size or licensing. Each project notebook references its input data under `capstones/<Project_Name>/data/`. If you want to re-run a notebook end-to-end, download the corresponding dataset into that folder.

Small datasets (< 1 MB) are included in the repo directly.

## Excluded files

### Formula 1 Data Analysis

**Missing**: `capstones/Formula1_Data_Analysis/data/lap_times.csv` (~17 MB)

- Source: [Ergast F1 Database](http://ergast.com/mrd/db/)
- Download the full zip (`f1db_csv.zip`), extract, and copy `lap_times.csv` into the `data/` folder
- Other F1 CSVs (`circuits.csv`, `races.csv`, `drivers.csv`, etc.) are included in the repo

### Movie Database ETL Pipeline

**Missing**:
- `capstones/Movie_Database_ETL_Pipeline/data/title.basics.tsv.gz` (~188 MB)
- `capstones/Movie_Database_ETL_Pipeline/data/title.ratings.tsv.gz` (~7 MB)

- Source: [IMDb Datasets](https://datasets.imdbws.com/)
- Download `title.basics.tsv.gz` and `title.ratings.tsv.gz` directly (no auth required)
- Place both files under `data/` without decompressing — pandas reads `.tsv.gz` directly

### Customer Analytics

**Missing**: `capstones/Customer_Analytics_Churn_CLV/data/segmentation/online12M.csv` (~6 MB)

- Source: [UCI Online Retail](https://archive.ics.uci.edu/ml/datasets/online+retail)
- Download the Excel file, convert to CSV, and rename as `online12M.csv`
- The two other datasets (`Telco-Customer-Churn.csv`, `Marketing-Customer-Value-Analysis.csv`) are included in the repo

### Book Recommender System

**Missing**:
- `capstones/Book_Recommender_System/data/book_tags.csv` (~16 MB)
- `capstones/Book_Recommender_System/data/ratings.csv` (~14 MB)

- Source: [goodbooks-10k on Kaggle](https://www.kaggle.com/datasets/zygmunt/goodbooks-10k)
- Download the Kaggle zip, extract, and copy both files into `data/`
- Other files (`books.csv`, `tags.csv`, `overviews/*.txt`) are included in the repo

### Airbnb Valencia BI

**Missing**: `capstones/Airbnb_Valencia_BI/data/listings.csv` and `reviews.csv`

- Source: [Inside Airbnb — Valencia](https://insideairbnb.com/get-the-data/)
- Download the `listings.csv.gz` and `reviews.csv.gz` files for Valencia
- Decompress and place under `data/`
- License: Inside Airbnb is CC BY 4.0 but redistribution of the raw data is not permitted

### Sentiment Analysis (Spark)

**Missing**: The full `amazon-reviews-pds-parquet` bundle (~10 GB total)

- Source: [Amazon Reviews PDS on AWS](https://s3.amazonaws.com/amazon-reviews-pds/readme.html) (requester-pays S3 bucket)
- This capstone is designed to run on AWS Glue with data in S3 — local execution is not practical for the full dataset
- The notebook provides a `product_category=Electronics` filter that reduces the working set to ~3M rows

## Small datasets (already in the repo)

These are small enough to ship and are committed directly:

- Capstone I: all other F1 CSVs (drivers, races, results, circuits, constructors, status, ...)
- Capstone III: `df_credits.csv`, `df_movies.csv`, `df_people.csv` (movie metadata, ~380 KB each)
- Capstone VIII: `books.csv`, `tags.csv`, `overviews/*.txt` (9,956 plot summaries)
- Capstone XIII: `laureates-1901-2019.parquet`, `nobelPrizes-1901-2019.parquet` (historical Nobel backfill)
- Capstone XIV: `heart.csv` (heart disease dataset, 36 KB)
