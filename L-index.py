#!/usr/bin/env python3
# coding: utf-8


import scholarly
import datetime
import math
import logging
import time
import os
import re
from difflib import SequenceMatcher
from fpdf import FPDF
from fpdf.enums import XPos, YPos, Align

try:
    from scholarly._navigator import MaxTriesExceededException
except ImportError:
    try:
        from scholarly._proxy_generator import MaxTriesExceededException
    except ImportError:
        MaxTriesExceededException = Exception
        logging.warning("Could not import specific MaxTriesExceededException from scholarly. Rate limit errors might not be caught precisely.")


MAX_SEARCH_RESULTS_TO_CHECK = 10
NAME_SIMILARITY_THRESHOLD = 0.85
SINGLE_RESULT_SIMILARITY_THRESHOLD = 0.75

MAX_PUBS_TO_PROCESS = 100 
TOP_N_PUBS_TO_SAVE_IN_REPORT = 15
OUTPUT_DIR = "L-index calculations"

logger = logging.getLogger()

LARGE_GROUP_KEYWORDS = ["consortium", "consortia", "group", "collaboration", "society", "association"]

log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
if logger.hasHandlers():
    logger.handlers.clear()
logger.setLevel(logging.INFO)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)

def sanitize_filename(name):
    """Removes or replaces characters unsuitable for filenames."""
    s = re.sub(r'[^\w\-\.]+', '_', name)
    s = re.sub(r'_+', '_', s).strip('_')
    return s if s else "invalid_name"

def count_authors(author_string):
    """Estimates the number of authors from a string."""
    if not author_string:
        return None
    if isinstance(author_string, (list, tuple)):
        author_string = ' and '.join(map(str, author_string))
        if not author_string:
            return None

    author_string_lower = author_string.lower()
    author_string_padded = f' {author_string_lower} '

    temp_string = author_string_lower.replace(' and ', ',').replace(';', ',')
    parts = [part.strip() for part in temp_string.split(',') if part.strip()]
    base_count = max(1, len(parts))

    additional_count = 0
    if ' et al' in author_string_padded: additional_count += 3
    found_large_group = False
    for keyword in LARGE_GROUP_KEYWORDS:
        if f' {keyword} ' in author_string_padded:
            found_large_group = True; break
    if found_large_group: additional_count += 50
    return base_count + additional_count

def encode_string_for_pdf(text):
    """Encodes text to latin-1, replacing unsupported characters, for PDF compatibility."""
    if text is None:
        return ""
    try:
        text_str = str(text)
        return text_str.encode('latin-1', 'replace').decode('latin-1')
    except Exception:
        try:
            return str(text).encode('ascii', 'replace').decode('ascii')
        except Exception:
            return "Encoding Error"


class PDF(FPDF):
    def header(self):
        pass

    def chapter_title(self, title):
        self.set_font('Helvetica', 'B', 12)
        # Encode title safely
        safe_title = encode_string_for_pdf(title)
        self.cell(0, 8, safe_title, border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)

    def chapter_body(self, data, is_list=False):
        self.set_font('Helvetica', '', 10)
        if is_list:
            for item in data:
                safe_item = encode_string_for_pdf(f"- {item}")
                self.multi_cell(0, 5, safe_item, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            safe_data = encode_string_for_pdf(data)
            self.multi_cell(0, 5, safe_data, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln()

    def key_value(self, key, value, is_link=False, link_url=""):
        self.set_font('Helvetica', 'B', 10)
        key_width = 30
        safe_key = encode_string_for_pdf(key + ":")
        self.cell(key_width, 6, safe_key, border=0, align='L', new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.set_font('Helvetica', '', 10)
        current_x = self.get_x()
        if value:
            processed_value = encode_string_for_pdf(value)
            if is_link and link_url:
                 self.set_text_color(0, 0, 255); self.set_font('', 'U')
                 self.set_x(current_x)
                 self.write(6, processed_value, link_url)
                 self.set_font('', ''); self.set_text_color(0, 0, 0)
                 self.ln(6)
            else:
                 self.set_x(current_x)
                 value_width = self.w - self.l_margin - self.r_margin - key_width
                 self.multi_cell(value_width, 6, processed_value, border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
             self.set_x(current_x)
             self.cell(0, 6, "N/A", border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def publication_table(self, header, data):
        self.set_font('Helvetica', 'B', 8)
        total_width = self.w - 2 * self.l_margin
        base_pcts = [0.04, 0.08, 0.08, 0.08, 0.05, 0.06]
        min_widths = [8, 12, 12, 12, 8, 10]
        col_widths = [max(min_w, total_width * pct) for min_w, pct in zip(min_widths, base_pcts)]
        title_width = max(20, total_width - sum(col_widths))
        col_widths.append(title_width)
        current_total = sum(col_widths)
        if current_total > total_width:
            scale_factor = total_width / current_total
            col_widths = [w * scale_factor for w in col_widths]

        for i, title in enumerate(header):
            align_val = Align.C
            new_x_pos = XPos.RIGHT if i < len(header) - 1 else XPos.LMARGIN
            new_y_pos = YPos.TOP if i < len(header) - 1 else YPos.NEXT
            header_text = encode_string_for_pdf(title)
            self.cell(col_widths[i], 7, header_text, border=1, align=align_val, new_x=new_x_pos, new_y=new_y_pos)

        self.set_font('Helvetica', '', 8)
        for row in data: 
            y_start = self.get_y()

            title_chars_per_line_est = col_widths[6] * 2 if col_widths[6] > 0 else 1
            title_lines = math.ceil(len(str(row[6])) / title_chars_per_line_est) if title_chars_per_line_est > 0 else 1
            needed_height = max(5, title_lines * 4)

            if y_start + needed_height > self.h - self.b_margin:
                self.add_page()
                self.set_font('Helvetica', 'B', 8)
                for i, title_h in enumerate(header):
                    align_val = Align.C
                    new_x_pos = XPos.RIGHT if i < len(header) - 1 else XPos.LMARGIN
                    new_y_pos = YPos.TOP if i < len(header) - 1 else YPos.NEXT
                    header_text_new = encode_string_for_pdf(title_h)
                    self.cell(col_widths[i], 7, header_text_new, border=1, align=align_val, new_x=new_x_pos, new_y=new_y_pos)
                self.set_font('Helvetica', '', 8)
                y_start = self.get_y()

            current_max_y = y_start
            align_map = [Align.R, Align.R, Align.R, Align.R, Align.R, Align.C, Align.L]
            current_x = self.l_margin
            for idx, (cell_data, width, align_val) in enumerate(zip(row, col_widths, align_map)):
                self.set_xy(current_x, y_start)
                processed_data = encode_string_for_pdf(cell_data)
                self.multi_cell(width, 5, processed_data, border=0, align=align_val)
                current_max_y = max(current_max_y, self.get_y())
                current_x += width

            self.set_y(y_start)
            x = self.l_margin
            self.line(x, y_start, x, current_max_y)
            for w in col_widths:
                x += w
                self.line(x, y_start, x, current_max_y)
            self.line(self.l_margin, current_max_y, self.w - self.r_margin, current_max_y)
            self.set_y(current_max_y)


def save_results_to_pdf(filename, author_details, l_index, processed_count, total_pubs_reported, top_pubs, was_rate_limited, skips_info):
    """Saves the calculation results to a PDF file with the updated format."""
    try:
        pdf = PDF(orientation='L', unit='mm', format='A4')
        pdf.add_page()

        author_name = author_details.get('name', 'N/A')
        pdf.set_font('Helvetica', 'B', 14)
        pdf.multi_cell(0, 10, author_name, border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.set_font('Helvetica', '', 10)
        affiliation = author_details.get('affiliation')
        if affiliation:
            pdf.multi_cell(0, 5, affiliation, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else: pdf.ln(1)

        interests = author_details.get('interests')
        if interests:
            interests_str = ", ".join(interests)
            pdf.multi_cell(0, 5, interests_str, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else: pdf.ln(1)

        profile_url = None
        scholar_id = author_details.get('scholar_id')
        if scholar_id:
            profile_url = f"https://scholar.google.com/citations?user={scholar_id}"
            pdf.set_text_color(0, 0, 255); pdf.set_font('', 'U')
            pdf.cell(0, 5, profile_url, link=profile_url, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font('', ''); pdf.set_text_color(0, 0, 0)
        else: pdf.ln(1)

        pdf.ln(5)

        if was_rate_limited:
            pdf.set_text_color(255, 0, 0); pdf.set_font('Helvetica', 'B', 10)
            pdf.multi_cell(0, 5, "*** WARNING: Processing aborted early due to Google Scholar rate limit (429 errors). Results are based on incomplete data. ***", border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0); pdf.ln(2)

        pdf.key_value("L-index", f"{l_index:.2f}" if l_index is not None else "Error")

        pdf.set_font('Helvetica', 'I', 9)
        current_date_str = datetime.datetime.now().strftime("%d %B %Y")
        calc_basis_str = f"Calculated on {current_date_str} based on the {total_pubs_reported} most cited publications"
        pdf.multi_cell(0, 5, calc_basis_str, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font('Helvetica', '', 10)
        pdf.ln(1)

        pdf.ln(5)

        pubs_to_show_in_table = top_pubs[:TOP_N_PUBS_TO_SAVE_IN_REPORT]
        pdf.chapter_title(f"Top {len(pubs_to_show_in_table)} Contributing Publications")

        if not pubs_to_show_in_table:
            pdf.cell(0, 6, "(No publications processed had a contribution score > 0 or process was stopped early)", border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            header = ['#', 'Score', 'Cites', 'Authors', 'Age', 'Year', 'Title']
            table_data = []
            for i, pub_data in enumerate(pubs_to_show_in_table):
                rank_str = f"{i+1}."
                term_str = f"{pub_data['term']:.1f}"
                c_str = str(pub_data['citations'])
                a_str = str(pub_data['authors'])
                y_str = str(pub_data['age'])
                yr_str = str(pub_data['year'])
                title_str = str(pub_data['title'])[:150] + ('...' if len(str(pub_data['title'])) > 150 else '')
                table_data.append([rank_str, term_str, c_str, a_str, y_str, yr_str, title_str])
            pdf.publication_table(header, table_data)

        pdf.ln(10)
        pdf.set_font('Helvetica','', 8)
        current_year = datetime.datetime.now().year
        footer1 = f"L-index Calculator by Aleksey V. Belikov, 2025"
        footer2 = f"L-index concept by Aleksey V. Belikov & Vitaly V. Belikov, 2015"
        pdf.cell(0, 5, footer1, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.cell(0, 5, footer2, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.set_font('Helvetica', 'B', 8)
        pdf.cell(0, 5, " ", align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font('Helvetica','', 8)
        citation_text = "Belikov AV and Belikov VV. A citation-based, author- and age-normalized, logarithmic index for evaluation of individual researchers independently of publication counts. F1000Research 2015, 4:884"
        citation_url = "https://doi.org/10.12688/f1000research.7070.1"
        pdf.multi_cell(0, 4, citation_text, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 255); pdf.set_font('', 'U')
        pdf.cell(0, 4, f"({citation_url})", align='L', link=citation_url, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0); pdf.set_font('', '')

        pdf.output(filename)
        logger.info(f"Results successfully saved to PDF: {filename}")

    except Exception as e:
        logger.error(f"Failed to generate PDF report: {e}", exc_info=True)
        print(f"\nError: Could not generate PDF report '{filename}'. Check logs.")


def calculate_l_index(author_name_or_id, max_pubs_limit):
    """Searches for an author, calculates their L-Index, and fetches details."""
    preliminary_index_I = 0.0
    processed_pubs_count = 0
    author_details = {'name': 'N/A', 'affiliation': None, 'interests': [], 'scholar_id': None, 'citedby': 'N/A'}
    publication_details = []
    rate_limited = False
    total_pubs_reported = 0
    i = -1 

    try:
        logger.info(f"Searching for author: {author_name_or_id}")
        is_id_search = bool(re.match(r'^[\w-]{12}$', author_name_or_id))
        author_to_process = None

        if is_id_search:
            try:
                author_stub = scholarly.scholarly.search_author_id(author_name_or_id, filled=False)
                if not author_stub:
                    raise ValueError(f"No author found for ID '{author_name_or_id}'.")
                author_details['scholar_id'] = author_stub.get('scholar_id')
                author_details['name'] = author_stub.get('name', 'Name Not Found')
                author_to_process = author_stub
                if not author_details['scholar_id']: raise ValueError("Author ID search returned result without scholar_id.")
                logger.info(f"Found author by ID: {author_details['name']} (ID: {author_details['scholar_id']})")
            except MaxTriesExceededException as rt_err: logger.error(f"Rate limit during author ID lookup: {rt_err}. Aborting."); rate_limited = True; return None, author_details, 0.0, 0, 0, [], rate_limited, i
            except StopIteration:
                 logger.error(f"No author found for ID '{author_name_or_id}'. ID might be invalid or profile private/removed.")
                 return None, author_details, 0.0, 0, 0, [], rate_limited, i
            except Exception as e: logger.error(f"Failed during author ID lookup: {e}", exc_info=False); return None, author_details, 0.0, 0, 0, [], rate_limited, i
        else: 
             potential_authors = []
             try:
                  search_query = scholarly.scholarly.search_author(author_name_or_id)
                  for idx in range(MAX_SEARCH_RESULTS_TO_CHECK):
                     try:
                         auth = next(search_query, None)
                         if auth is None: break
                         if auth and 'scholar_id' in auth: potential_authors.append(auth)
                         elif auth: logger.warning(f"Search result missing 'scholar_id': {auth.get('name', 'N/A')}")
                     except StopIteration: break
                     except MaxTriesExceededException as rt_err_inner: logger.error(f"Rate limit during author search iteration {idx+1}: {rt_err_inner}. Stopping search."); rate_limited = True; break
                     except Exception as e_inner: logger.error(f"Error during author search iteration {idx+1}: {e_inner}. Stopping search."); break 
                  logger.info(f"Found {len(potential_authors)} potential author(s) with IDs.")
             except MaxTriesExceededException as rt_err: logger.error(f"Rate limit during initial author search setup: {rt_err}. Aborting."); rate_limited = True
             except StopIteration: logger.info(f"Found {len(potential_authors)} potential author(s) with IDs (StopIteration caught).")
             except Exception as e: logger.error(f"Error during author search setup: {e}", exc_info=False); potential_authors = []

             if rate_limited: return None, author_details, 0.0, 0, 0, [], rate_limited, i
             if not potential_authors: logger.error(f"Author '{author_name_or_id}' not found or no suitable matches retrieved."); return None, author_details, 0.0, 0, 0, [], rate_limited, i

             best_match_author = None; highest_ratio = 0.0; query_lower = author_name_or_id.lower()
             logger.info("Evaluating potential matches:")
             for pa in potential_authors:
                 name_lower = pa.get('name', '').lower();
                 if not name_lower: continue
                 ratio = SequenceMatcher(None, query_lower, name_lower).ratio()
                 logger.info(f"  - Candidate: '{pa.get('name', 'N/A')}', ID: {pa.get('scholar_id', 'N/A')}, Aff: {pa.get('affiliation', 'N/A')}, Ratio: {ratio:.3f}")
                 if ratio > highest_ratio:
                     highest_ratio = ratio
                     best_match_author = pa
                 elif ratio == highest_ratio and best_match_author:
                     logger.info(f"  - Note: Equal ratio {ratio:.3f} found for '{pa.get('name', 'N/A')}' and '{best_match_author.get('name', 'N/A')}'. Keeping first best match.")
                     pass

             selected_author_final = None
             effective_threshold = NAME_SIMILARITY_THRESHOLD
             if len(potential_authors) == 1:
                 effective_threshold = SINGLE_RESULT_SIMILARITY_THRESHOLD
                 logger.info(f"Only one result found. Using adjusted threshold for selection: {effective_threshold:.2f}")

             if best_match_author and highest_ratio >= effective_threshold:
                 selected_author_final = best_match_author
                 logger.info(f"Selected author based on highest ratio >= threshold: {selected_author_final['name']} (Ratio: {highest_ratio:.3f})")
             else:
                 logger.warning(f"Could not find a confident match. Best match '{best_match_author.get('name', 'N/A') if best_match_author else 'None'}' had ratio {highest_ratio:.3f} (Threshold: {effective_threshold:.2f}).")
                 logger.error(f"Failed to identify a sufficiently similar author match.")
                 return None, author_details, 0.0, 0, 0, [], rate_limited, i

             author_to_process = selected_author_final 
             author_details['scholar_id'] = author_to_process.get('scholar_id')
             author_details['name'] = author_to_process.get('name', 'Name Not Found') 

        if not author_to_process or not author_details.get('scholar_id'):
            logger.error("Author selection process failed to yield a valid author object or ID.")
            return None, author_details, 0.0, 0, 0, [], rate_limited, i

        logger.info(f"Fetching full profile details for {author_details.get('name', 'N/A')} (ID: {author_details.get('scholar_id')})...")
        author_filled_profile = None
        try:
            sections_to_fill = ['basics', 'indices', 'interests', 'coauthors', 'counts']
            author_filled_profile = scholarly.scholarly.fill(author_to_process, sections=sections_to_fill)

            author_details['name'] = author_filled_profile.get('name', author_details.get('name'))
            author_details['affiliation'] = author_filled_profile.get('affiliation', author_details.get('affiliation'))
            author_details['interests'] = author_filled_profile.get('interests', author_details.get('interests', []))
            author_details['citedby'] = author_filled_profile.get('citedby', author_details.get('citedby', 'N/A'))

            logger.info(f"Successfully fetched profile details. Name: '{author_details['name']}', Affiliation: '{author_details.get('affiliation', 'N/A')}', Total citations reported: {author_details['citedby']}")

        except MaxTriesExceededException as rt_err:
            logger.error(f"Rate limit occurred while fetching full profile details: {rt_err}. Proceeding with potentially incomplete author info (using stub data if available).")
            rate_limited = True
        except Exception as e:
            logger.error(f"Error filling author profile: {e}. Proceeding with potentially incomplete author info.", exc_info=False)

        logger.info(f"Fetching initial publication list (sorted by citedby, limit {max_pubs_limit})...")
        initial_pubs = []
        try:
            author_obj_for_pubs = author_filled_profile if author_filled_profile else author_to_process
            if 'publications' not in author_obj_for_pubs:
                logger.info("Filling publications section...")
                author_pubs_filled = scholarly.scholarly.fill(
                    author_obj_for_pubs,
                    sections=['publications'],
                    sortby='citedby',
                    publication_limit=max_pubs_limit
                )
            else: 
                 logger.info("Publications section already present, using existing data (up to limit).")
                 author_pubs_filled = author_obj_for_pubs

            if not author_pubs_filled or 'publications' not in author_pubs_filled or author_pubs_filled['publications'] is None:
                initial_pubs = []
            else:
                initial_pubs = author_pubs_filled.get('publications', [])[:max_pubs_limit]

        except MaxTriesExceededException as rt_err:
            logger.error(f"Rate limit occurred while fetching publication list: {rt_err}. Aborting calculation.")
            rate_limited = True
            return None, author_details, 0.0, 0, 0, [], rate_limited, i
        except Exception as e:
            logger.error(f"Error fetching publication list: {e}", exc_info=False)
            return None, author_details, 0.0, 0, 0, [], rate_limited, i

        total_pubs_reported = len(initial_pubs)
        if not initial_pubs and not rate_limited:
            logger.warning(f"No publications found for author {author_details.get('name')}. L-index will be 0.")
            return 0.0, author_details, 0.0, 0, total_pubs_reported, [], rate_limited, i

        pubs_to_process = initial_pubs
        num_selected = len(pubs_to_process)
        logger.info(f"Fetched {num_selected} publications (limit was {max_pubs_limit}). Starting processing...")
        current_year = datetime.datetime.now().year
        skipped_count_within_top_n = 0

        for i, pub_stub in enumerate(pubs_to_process):
            if rate_limited:
                 logger.warning(f"Stopping publication processing at pub {i+1} due to earlier rate limit.")
                 break

            pub_title_guess = pub_stub.get('bib', {}).get('title', 'Unknown Title')
            logger.info(f"Processing pub {i+1}/{num_selected}: '{pub_title_guess[:60]}...'")

            try:
                pub = None
                author_str = ''
                num_authors = 1 

                try:
                    pub = scholarly.scholarly.fill(pub_stub)
                    bib = pub.get('bib', {})
                    author_str = bib.get('author', '')
                    num_authors_temp = count_authors(author_str)
                    if num_authors_temp is None:
                        logger.warning(f"Could not reliably count authors for pub {i+1} ('{pub_title_guess[:50]}...'). Assuming 1 author.")
                        num_authors = 1
                    else:
                        num_authors = num_authors_temp
                except MaxTriesExceededException as rt_err:
                    logger.error(f"Rate limit hit while filling details for pub {i+1} ('{pub_title_guess[:50]}...'): {rt_err}. Aborting further processing.")
                    rate_limited = True
                    skipped_count_within_top_n += 1
                    break
                except Exception as fill_err:
                    logger.error(f"Failed to fill details for pub {i+1} ('{pub_title_guess[:50]}...'): {fill_err}. Will use stub data and assume 1 author.", exc_info=False)
                    pub = pub_stub
                    bib = pub_stub.get('bib', {})
                    author_str = bib.get('author', '') 
                    num_authors = 1 
                    logger.warning(f"Assuming 1 author for pub {i+1} due to fill error.")
                
                citations = pub.get('num_citations', pub_stub.get('num_citations', 0))
                pub_year_str = bib.get('pub_year', None)
                title = bib.get('title', 'Title Not Available')

                if pub_year_str is None:
                    logger.warning(f"Skipping pub {i+1} ('{title[:50]}...') due to missing publication year.")
                    skipped_count_within_top_n += 1
                    continue
                try:
                    pub_year = int(pub_year_str)
                    if pub_year > current_year + 2 or pub_year < 1800: 
                        logger.warning(f"Skipping pub {i+1} ('{title[:50]}...') due to potentially invalid year: {pub_year}.")
                        skipped_count_within_top_n += 1
                        continue
                except ValueError:
                    logger.warning(f"Skipping pub {i+1} ('{title[:50]}...') due to non-integer year format: '{pub_year_str}'.")
                    skipped_count_within_top_n += 1
                    continue

                
                citations = citations if citations is not None else 0

                age = max(1, current_year - pub_year + 1)

                denominator = num_authors * age

                term = citations / denominator

                pub_data = {
                    'term': term,
                    'title': title,
                    'year': pub_year,
                    'citations': citations,
                    'authors': num_authors,
                    'age': age
                }
                publication_details.append(pub_data)
                preliminary_index_I += term
                processed_pubs_count += 1

                if (processed_pubs_count % 25 == 0) and processed_pubs_count > 0: # Log more frequently if needed
                    logger.info(f"Processed {processed_pubs_count}/{num_selected} pubs...")

            except Exception as e:
                logger.error(f"Critical error processing pub {i+1} ('{pub_title_guess[:50]}...'): {e}", exc_info=False)
                skipped_count_within_top_n += 1

        if skipped_count_within_top_n > 0:
            logger.warning(f"Skipped {skipped_count_within_top_n} publications within the attempted set ({i+1} pubs) due to missing/invalid data or processing errors.")


        l_index = math.log(preliminary_index_I + 1)

        logger.info("Sorting processed publications by contribution score (term)...")
        sorted_contributors = sorted(publication_details, key=lambda p: p['term'], reverse=True)

        positive_term_contributors = [p for p in sorted_contributors if p['term'] > 0]
        logger.info(f"Identified {len(positive_term_contributors)} processed publications with a contribution score > 0.")

        top_contributing_list = sorted_contributors

        if rate_limited:
            logger.warning("Calculation finished BUT was affected or aborted early due to Google Scholar rate limiting.")
        else:
            logger.info("Calculation process completed.")
            if i + 1 < num_selected:
                logger.warning(f"Processing loop did not complete all {num_selected} fetched publications (stopped at {i+1}). This might indicate an error not caught as rate limit.")

        return l_index, author_details, preliminary_index_I, processed_pubs_count, total_pubs_reported, top_contributing_list, rate_limited, i

    except Exception as e:
        logger.error(f"An unexpected critical error occurred during the main calculation process: {e}", exc_info=True)
        if 'i' not in locals(): i = -1
        return None, author_details, preliminary_index_I, processed_pubs_count, total_pubs_reported, [], rate_limited, i


if __name__ == "__main__":
    print("-" * 60)
    print("L-index Calculator by Aleksey V. Belikov")
    print("-" * 60)
    max_pubs_limit = MAX_PUBS_TO_PROCESS
    print("-" * 60)
    print("IMPORTANT NOTES:")
    print("1. Results are entirely dependent on the accuracy, completeness and public availability of the scientist's Google Scholar profile")
    print("2. While the script attempts to find the best match for an author's name, errors can occur, especially for common names")
    print("3. Check the affiliation, keywords and top publications in the output pdf to verify that the correct scientist has been identified")
    print("4. Using the Google Scholar ID is recommended, it can be found at the end of the profile URL")
    print("5. The count_authors function estimates author numbers, which can sometimes be imprecise for complex author strings or large consortia")
    print(f"6. Calculation is based on the {max_pubs_limit} most cited publications found (or fewer if author has less)")
    print("7. This can be changed by modifying MAX_PUBS_TO_PROCESS parameter in the code")
    print("8. Extensive requests can lead to temporary IP blocks (rate limiting) from Google Scholar, so it is recommended to keep MAX_PUBS_TO_PROCESS to 100")
    print("9. It is recommended to wait (hours, or even a day) if you encounter persistent rate limiting, or try a different IP address or a proxy")
    print("10. Selecting too low a MAX_PUBS_TO_PROCESS value will lead to underestimation of the L-index")
    print("11. Nevertheless, 100 most cited publications capture the bulk of the L-index, even for authors with many hundreds of publications")
    print("12. Always compare scientists using the same MAX_PUBS_TO_PROCESS value to calculate their L-indices")
    print(f"13. A PDF report including the top {TOP_N_PUBS_TO_SAVE_IN_REPORT} contributing publications will be saved in the '{OUTPUT_DIR}' directory")
    print("14. The number of the top contributing publications in the pdf report can be changed by modifying TOP_N_PUBS_TO_SAVE_IN_REPORT parameter in the code")
    print("15. The PDF generation uses latin-1 encoding with replacements for unsupported characters. Some special characters in names or titles might not render perfectly")
    print("-" * 60)

    author_query = input("Enter Author Name or Google Scholar ID: ")

    if not author_query:
        print("No author name or ID provided. Exiting.")
    else:
        l_index, author_data, prelim_I, processed_count, total_reported, top_contrib_pubs, was_rate_limited, last_attempted_index = calculate_l_index(
            author_query,
            max_pubs_limit
        )

        author_full_name = author_data.get('name')
        if author_full_name is None or author_full_name == 'N/A':
             author_full_name_display = "N/A (Could not be determined)"
        else:
             author_full_name_display = author_full_name

        if was_rate_limited:
            print("\n--- WARNING: RATE LIMITED ---")
            print(f"Processing may have stopped early due to Google Scholar rate limits.")
            print("Results shown below might be based on INCOMPLETE data gathered before the limit was hit.")
            print(f"If errors persist, please wait a significant amount of time (e.g., hours) before trying again.")
            if author_full_name_display.startswith("N/A"):
                 print("Rate limit may have occurred before the author could be definitively identified.")
            print("-" * 60)

        if author_data.get('scholar_id'):
            if l_index is None:
                 print(f"\n--- Calculation Error ---")
                 print(f"Author Identified: {author_full_name_display} (ID: {author_data.get('scholar_id', 'N/A')})")
                 print(f"Affiliation:       {author_data.get('affiliation', 'N/A')}")
                 print(f"Could not complete L-index calculation due to errors after author identification.")
                 print(f"(Attempted to process up to publication {last_attempted_index + 1 if last_attempted_index != -1 else 'N/A'} out of {total_reported} fetched).")
                 print(f"(Successfully processed {processed_count} publications before error/stop).")
                 print("Please check the script's log output for detailed error messages.")

            else:
                print("\n--- Results Summary ---")
                if was_rate_limited: print("(NOTE: Results based on potentially INCOMPLETE data due to rate limiting)")
                print(f"Author Identified: {author_full_name_display}")
                print(f"Affiliation:       {author_data.get('affiliation', 'N/A')}")
                print(f"Interests:         {', '.join(author_data.get('interests', [])) if author_data.get('interests') else 'N/A'}")
                scholar_id = author_data.get('scholar_id')
                print(f"Scholar Profile:   {'https://scholar.google.com/citations?user=' + scholar_id if scholar_id else 'N/A'}")
                print(f"L-Index:           {l_index:.2f}")
                print(f"Calculation Basis: The {total_reported} most cited publications fetched from Google Scholar.")
                print(f"Pubs Processed:    {processed_count} / {total_reported} (Fetched)")

                pubs_attempted_in_loop = last_attempted_index + 1 if last_attempted_index != -1 else 0
                skips_within_attempted_group = max(0, pubs_attempted_in_loop - processed_count)
                unreached_due_to_stop = max(0, total_reported - pubs_attempted_in_loop)
                total_unprocessed_within_fetched = skips_within_attempted_group + unreached_due_to_stop

                if total_unprocessed_within_fetched > 0:
                     print(f"Note: {total_unprocessed_within_fetched} publications within the fetched {total_reported} were not fully processed.")
                     if skips_within_attempted_group > 0:
                         print(f"      - {skips_within_attempted_group} were attempted (up to pub #{pubs_attempted_in_loop}) but failed detail fetch, validation, or had invalid data (check logs).")
                     if unreached_due_to_stop > 0:
                         reason = "rate limit or other processing stop" if was_rate_limited else "an early processing stop (check logs)"
                         print(f"      - {unreached_due_to_stop} were not reached in the processing loop (after pub #{pubs_attempted_in_loop}) likely due to {reason}.")

                skips_info_pdf = {
                    'total': total_unprocessed_within_fetched,
                    'failed_processing': skips_within_attempted_group,
                    'unreached_limit': unreached_due_to_stop
                 }

                if author_full_name and author_full_name != 'N/A':
                    try:
                        safe_filename_base = sanitize_filename(f"{author_full_name}_{author_data.get('scholar_id', 'NoID')}")
                        status_tag = "_RATE_LIMITED" if was_rate_limited else ""
                        date_str = datetime.date.today().isoformat()
                        pdf_filename = os.path.join(OUTPUT_DIR, f"{safe_filename_base}_L-Index_BasedOn{max_pubs_limit}{status_tag}_{date_str}.pdf")
                        os.makedirs(OUTPUT_DIR, exist_ok=True)

                        save_results_to_pdf(
                            pdf_filename,
                            author_data,
                            l_index,
                            processed_count,
                            total_reported,
                            top_contrib_pubs,
                            was_rate_limited,
                            skips_info_pdf
                        )
                    except Exception as pdf_err:
                        pass 
                else:
                    logger.warning("Skipping PDF generation because a valid author name could not be determined for the filename.")
                    print("\nWarning: PDF report generation skipped as author name was not fully determined.")

        elif not was_rate_limited:
             print("\n--- Author Not Found ---")
             print("Could not calculate L-index.")
             print("Reason: Author not found or no confident match identified via search.")
             print("Please check the spelling or try the Google Scholar ID if known.")

        print("-" * 60)

