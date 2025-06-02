<div align='left'>

<h1>L-index Calculator</h1>

</div>

An easy to use tool to calculate the L-index, an academic citation metric, as proposed by Aleksey and Vitaly Belikov:

    Aleksey V. Belikov and Vitaly V. Belikov (2015) A citation-based, author- and age-normalized, logarithmic index for evaluation of individual researchers independently of publication counts. F1000Research 4:884 https://doi.org/10.12688/f1000research.7070.2

https://doi.org/10.12688/f1000research.7070.2

This tool can be run as a standard Python script (`L-index.py`) or  as a Jupyter Notebook (`L-index.ipynb`)

![L-index values for notable scientists](./L-index.png)

## Features

*   Calculates the L-index based on a scientist's Google Scholar profile
*   Uses 100 (configurable) most cited publications to avoid rate limiting and IP ban by Google Scholar
*   Generates a detailed PDF report including:
    *   Author's profile information (name, affiliation, keywords, Google Scholar profile link).
    *   L-index, number of papers used for calculation and date of calculation
    *   A table of 100 (configurable) top contributing publications sorted by the L-index score with their individual scores, citation counts, author counts, ages, publication years, and titles
*   Provides console output with progress, warnings and summary

##  Getting Started

### Prerequisites

- [Python](https://www.python.org/downloads/) 3.7 or higher
- [Jupyter](https://jupyter.org/install) Notebook or JupyterLab (Optional)

### Dependencies

- [Scholarly](https://pypi.org/project/scholarly/) 1.7.11 - for interfacing with Google Scholar to retrieve publication and citation data
- [fpdf2](https://pypi.org/project/fpdf2/) 2.8.3 - for creating PDF documents

These will be installed automatically via the `requirements.txt` file

### Installation

Open the terminal in the desired folder (e.g. `My scripts`) and run:
```bash
git clone https://github.com/alekseybelikov/L-index.git
```

```bash
cd L-index
```

```bash
pip3 install -r requirements.txt
```
## Running
1. Open the terminal in the `L-index` folder and run:
```bash
python3 L-index.py
```
or open `L-index.ipynb` in Jupiter Notebook or JupyterLab and execute the cell

2. Follow the instructions given by the script

The script will display some initial notes and then prompt you:

    Enter the scientist's Google Scholar ID:

Type the scientist's Google Scholar ID (e.g., `qc6CJjYAAAAJ`, can be found at the end of their profile URL) and press Enter

The script will then attempt to find the scientist, fetch their publications, calculate the L-index, and save a PDF report in the `L-index calculations` folder 




## Configuration (In-Script)

Several key parameters can be configured by editing the `L-index.py` script or  the cell of `L-index.ipynb`:

*   `MAX_PUBS_TO_PROCESS`: The maximum number of scientist's most cited publications to fetch and process for the L-index calculation (default: `100`). **Caution: High values (>100) increase processing time and risk of hitting Google Scholar rate limits. Low values (<50) will underestimate the L-index. Always compare scientists with the same setting used to calculate their L-indices.**
*   `TOP_N_PUBS_TO_SAVE_IN_REPORT`: Number of top contributing publications to include in the PDF report table (default: `100`)
*   `OUTPUT_DIR`: Directory where PDF reports are saved (default: `"L-index calculations"`)

## Important Notes & Limitations

1.  **Google Scholar Dependency:** Results are entirely dependent on the accuracy, completeness, and public availability of the scientist's Google Scholar profile.
2. **Information Completeness** Publications with missing author information or publication year will be skipped and a warning will be issued. Missing citation counts will be treated as 0 citations. If the script identifies one of the keywords for a large group of authors in the "authors" database field, it will add 50 authors to the author count, because the actual number of authors is unknown. Keywords used for this are "consortium", "consortia", "group", "collaboration", "society", "association", "initiative", "network", "committee", "investigators", "program", "programm", "team", "atlas", "international".
3.  **Publication Limit:** The calculation is based on a configurable number (`MAX_PUBS_TO_PROCESS`, default 100) of scientist's most cited publications (or fewer if the scientist has less or some data were missing). Scientists with more publications might have their L-index affected by this limit. Nevertheless, we demonstrated that 50-100 most cited publications capture the bulk of the L-index, even for scientists with many hundreds of publications. Always compare scientists using the same `MAX_PUBS_TO_PROCESS` value to calculate their L-indices.
4.  **Rate Limiting:** Google Scholar enforces rate limits on requests. Extensive or rapid use of this script (especially for many scientists or with a very high `MAX_PUBS_TO_PROCESS`) can lead to temporary IP blocks (HTTP 429 errors). The script attempts to handle this gracefully but may provide incomplete results if severely rate-limited. It is recommended to wait (hours, or even a day) if you encounter persistent rate limiting, or try a different IP address or a proxy.


## License

Distributed under the AGPL-3.0 license. See the LICENSE file for more information.

## Citation

If you use this L-index concept or calculator in your work, please cite the original publication:

    Aleksey V. Belikov and Vitaly V. Belikov (2015) A citation-based, author- and age-normalized, logarithmic index for evaluation of individual researchers independently of publication counts. F1000Research 4:884 https://doi.org/10.12688/f1000research.7070.2
    
and this tool:

    Aleksey V. Belikov (2025) L-Index Calculator: A Python tool for academic citation analysis. Zenodo https://doi.org/10.5281/zenodo.15356377
    
[![DOI](https://zenodo.org/badge/978968980.svg)](https://doi.org/10.5281/zenodo.15356377)

## Contact

Aleksey V. Belikov - [@AlekseyVBelikov](https://x.com/AlekseyVBelikov) - belikov.research@gmail.com

Project Link: [https://github.com/alekseybelikov/L-index](https://github.com/alekseybelikov/L-index)
