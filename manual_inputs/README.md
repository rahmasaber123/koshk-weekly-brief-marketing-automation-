# Manual fallback inputs

Used only when the API source fails. To use one, copy the matching `.example` file to `.csv` and fill it in by hand.
Delete the `.csv` afterwards: if it stays, it will be reused on the next failure.
The brief marks the section **partial** and says the data came from a manual CSV.

- `ads.csv`: columns `competitor, page_name, ad_archive_id, start_date, ad_text, image_path, ad_library_link`.
  `image_path` is optional and relative to the repo root (e.g. `manual_inputs/chipsy1.jpg`).
- `trends.csv`: columns `date (YYYY-MM-DD), term, value`. Copy the values exactly as shown in Google Trends
  (Egypt, past 90 days). `<1` is allowed and is treated as text, not as a number.
