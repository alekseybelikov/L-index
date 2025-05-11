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
TOP_N_PUBS_TO_SAVE_IN_REPORT = 100
OUTPUT_DIR = "L-index calculations"

logger = logging.getLogger()

LARGE_GROUP_KEYWORDS = ["consortium", "consortia", "group", "collaboration", "society", "association", "initiative", "network", "committee", "investigators", "program", "programm", "team", "atlas", "international"]

log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
if logger.hasHandlers():
    logger.handlers.clear()
logger.setLevel(logging.INFO)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)

def sanitize_filename(name):
    s = re.sub(r'[^\w\-\.]+', '_', name)
    s = re.sub(r'_+', '_', s).strip('_')
    return s if s else "invalid_name"

def count_authors(author_string):
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
    if text is None:
        return ""
    return str(text)


class PDF(FPDF):
    def header(self):
        pass

    def chapter_title(self, title):
        self.set_font('DejaVu', 'B', 12)
        safe_title = encode_string_for_pdf(title)
        self.cell(0, 8, safe_title, border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)

    def chapter_body(self, data, is_list=False):
        self.set_font('DejaVu', '', 10)
        if is_list:
            for item in data:
                safe_item = encode_string_for_pdf(f"- {item}")
                self.multi_cell(0, 5, safe_item, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            safe_data = encode_string_for_pdf(data)
            self.multi_cell(0, 5, safe_data, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln()

    def key_value(self, key, value, is_link=False, link_url=""):
        self.set_font('DejaVu', 'B', 10)
        key_width = 30
        safe_key = encode_string_for_pdf(key + ":")
        self.cell(key_width, 6, safe_key, border=0, align='L', new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.set_font('DejaVu', '', 10)
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
        self.set_font('DejaVu', 'B', 8) # Changed from Helvetica
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

        self.set_font('DejaVu', '', 8)
        for row in data:
            y_start = self.get_y()

            title_chars_per_line_est = col_widths[6] * 2 if col_widths[6] > 0 else 1
            title_lines = math.ceil(len(str(row[6])) / title_chars_per_line_est) if title_chars_per_line_est > 0 else 1
            needed_height = max(5, title_lines * 4)

            if y_start + needed_height > self.h - self.b_margin:
                self.add_page()
                self.set_font('DejaVu', 'B', 8)
                for i_h, title_h in enumerate(header):
                    align_val = Align.C
                    new_x_pos = XPos.RIGHT if i_h < len(header) - 1 else XPos.LMARGIN
                    new_y_pos = YPos.TOP if i_h < len(header) - 1 else YPos.NEXT
                    header_text_new = encode_string_for_pdf(title_h)
                    self.cell(col_widths[i_h], 7, header_text_new, border=1, align=align_val, new_x=new_x_pos, new_y=new_y_pos)
                self.set_font('DejaVu', '', 8)
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


def save_results_to_pdf(filename, author_details, l_index, processed_count, total_pubs_reported, top_pubs, was_rate_limited, skips_summary_data):
    try:
        pdf = PDF(orientation='L', unit='mm', format='A4')

        try:
            pdf.add_font('DejaVu', '', 'DejaVuSans.ttf', uni=True)
            pdf.add_font('DejaVu', 'B', 'DejaVuSans-Bold.ttf', uni=True)
            pdf.add_font('DejaVu', 'I', 'DejaVuSans-Oblique.ttf', uni=True)
            pdf.add_font('DejaVu', 'BI', 'DejaVuSans-BoldOblique.ttf', uni=True)
        except RuntimeError as e:
            logger.error(f"Could not load DejaVu font: {e}. Cyrillic characters may not display correctly.")
            logger.error("Please ensure DejaVuSans.ttf, DejaVuSans-Bold.ttf, DejaVuSans-Oblique.ttf, and DejaVuSans-BoldOblique.ttf are in the script's directory or provide a full path.")
            logger.error("Falling back to Helvetica; Cyrillic support will be MISSING.")

        pdf.add_page()

        author_name = author_details.get('name', 'N/A')
        pdf.set_font('DejaVu', 'B', 14)
        pdf.multi_cell(0, 10, encode_string_for_pdf(author_name), border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.set_font('DejaVu', '', 10)
        affiliation = author_details.get('affiliation')
        if affiliation:
            pdf.multi_cell(0, 5, encode_string_for_pdf(affiliation), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else: pdf.ln(1)

        interests = author_details.get('interests')
        if interests:
            interests_str = ", ".join(interests)
            pdf.multi_cell(0, 5, encode_string_for_pdf(interests_str), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else: pdf.ln(1)

        profile_url = None
        scholar_id = author_details.get('scholar_id')
        if scholar_id:
            profile_url = f"https://scholar.google.com/citations?user={scholar_id}"
            pdf.set_text_color(0, 0, 255); pdf.set_font('', 'U')
            pdf.cell(0, 5, encode_string_for_pdf(profile_url), link=profile_url, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font('', ''); pdf.set_text_color(0, 0, 0)
        else: pdf.ln(1)

        pdf.ln(5)

        if was_rate_limited:
            pdf.set_text_color(255, 0, 0); pdf.set_font('DejaVu', 'B', 10)
            pdf.multi_cell(0, 5, encode_string_for_pdf("*** WARNING: Processing aborted or affected by Google Scholar rate limit (429 errors). Results may be based on incomplete data. ***"), border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0); pdf.ln(2)

        pdf.key_value("L-index", f"{l_index:.2f}" if l_index is not None else "Error")

        pdf.set_font('DejaVu', 'I', 9)
        current_date_str = datetime.datetime.now().strftime("%d %B %Y")
        calc_basis_str = f"Calculated on {current_date_str} based on the {total_pubs_reported} most cited publications fetched"
        pdf.multi_cell(0, 5, encode_string_for_pdf(calc_basis_str), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font('DejaVu', '', 10)
        pdf.ln(1)

        pdf.ln(3)
        total_skipped_in_pdf = sum(
            count for reason, count in skips_summary_data.items()
            if reason != 'processing_halted_by_rate_limit' and count > 0
        )
        halted_early_count_pdf = skips_summary_data.get('processing_halted_by_rate_limit', 0)

        if total_skipped_in_pdf > 0 or halted_early_count_pdf > 0:
            pdf.set_font('DejaVu', 'B', 10)
            pdf.cell(0, 6, "Publication Processing Notes:", border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font('DejaVu', '', 9)

            if total_skipped_in_pdf > 0:
                pdf.multi_cell(0, 5, encode_string_for_pdf(f"- Publications skipped due to missing/invalid data: {total_skipped_in_pdf}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                for reason, count in skips_summary_data.items():
                    if count > 0 and reason != 'processing_halted_by_rate_limit':
                        reason_text = reason.replace('_', ' ')
                        pdf.multi_cell(0, 4, encode_string_for_pdf(f"    - {count} due to: {reason_text}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            
            if halted_early_count_pdf > 0:
                pdf.multi_cell(0, 5, encode_string_for_pdf(f"- Publications not processed/completed due to rate limit or early stop: {halted_early_count_pdf}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(3)

        pdf.ln(5)

        pubs_to_show_in_table = top_pubs[:TOP_N_PUBS_TO_SAVE_IN_REPORT]
        pdf.chapter_title(f"Top {len(pubs_to_show_in_table)} Contributing Publications (among {processed_count} successfully processed)")


        if not pubs_to_show_in_table:
            pdf.cell(0, 6, "(No publications processed had a contribution score > 0 or processing was stopped/encountered issues)", border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
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
        pdf.set_font('DejaVu','', 8)
        current_year = datetime.datetime.now().year
        footer1 = f"L-index Calculator by Aleksey V. Belikov, 2025"
        footer2 = f"L-index concept by Aleksey V. Belikov & Vitaly V. Belikov, 2015"
        pdf.cell(0, 5, encode_string_for_pdf(footer1), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.cell(0, 5, encode_string_for_pdf(footer2), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.set_font('DejaVu', 'B', 8)
        pdf.cell(0, 5, " ", align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font('DejaVu','', 8)
        citation_text = "Belikov AV and Belikov VV. A citation-based, author- and age-normalized, logarithmic index for evaluation of individual researchers independently of publication counts. F1000Research 2015, 4:884"
        citation_url = "https://doi.org/10.12688/f1000research.7070.1"
        pdf.multi_cell(0, 4, encode_string_for_pdf(citation_text), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 255); pdf.set_font('', 'U')
        pdf.cell(0, 4, encode_string_for_pdf(f"({citation_url})"), align='L', link=citation_url, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0); pdf.set_font('', '')

        pdf.output(filename)
        logger.info(f"Results successfully saved to PDF: {filename}")

    except Exception as e:
        logger.error(f"Failed to generate PDF report: {e}", exc_info=True)
        print(f"\nError: Could not generate PDF report '{filename}'. Check logs.")


def calculate_l_index(author_name_or_id, max_pubs_limit):
    preliminary_index_I = 0.0
    processed_pubs_count = 0
    author_details = {'name': 'N/A', 'affiliation': None, 'interests': [], 'scholar_id': None, 'citedby': 'N/A'}
    publication_details = []
    rate_limited = False
    total_pubs_reported = 0
    i = -1
    
    skipped_details = {
        'author_field_empty': 0,
        'pub_year_missing': 0,
        'pub_year_invalid_format_or_range': 0,
        'processing_halted_by_rate_limit': 0,
        'other_critical_error_per_pub': 0
    }

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
            except MaxTriesExceededException as rt_err: logger.error(f"Rate limit during author ID lookup: {rt_err}. Aborting."); rate_limited = True; return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details
            except StopIteration:
                 logger.error(f"No author found for ID '{author_name_or_id}'. ID might be invalid or profile private/removed.")
                 return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details
            except Exception as e: logger.error(f"Failed during author ID lookup: {e}", exc_info=False); return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details
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

             if rate_limited: return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details
             if not potential_authors: logger.error(f"Author '{author_name_or_id}' not found or no suitable matches retrieved."); return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details

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
                 return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details

             author_to_process = selected_author_final
             author_details['scholar_id'] = author_to_process.get('scholar_id')
             author_details['name'] = author_to_process.get('name', 'Name Not Found')

        if not author_to_process or not author_details.get('scholar_id'):
            logger.error("Author selection process failed to yield a valid author object or ID.")
            return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details

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
            return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details
        except Exception as e:
            logger.error(f"Error fetching publication list: {e}", exc_info=False)
            return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details

        total_pubs_reported = len(initial_pubs)
        if not initial_pubs and not rate_limited:
            logger.warning(f"No publications found for author {author_details.get('name')}. L-index will be 0.")
            return 0.0, author_details, 0.0, 0, total_pubs_reported, [], rate_limited, skipped_details

        pubs_to_process = initial_pubs
        num_selected = len(pubs_to_process)
        logger.info(f"Fetched {num_selected} publications (limit was {max_pubs_limit}). Starting processing...")
        current_year = datetime.datetime.now().year

        for i, pub_stub in enumerate(pubs_to_process):
            if rate_limited and not skipped_details['processing_halted_by_rate_limit']:
                skipped_details['processing_halted_by_rate_limit'] = num_selected - i
                logger.warning(f"Skipping remaining {num_selected - i} publications processing due to rate limit flag set before or during this pub's processing.")
                break
            if skipped_details['processing_halted_by_rate_limit'] > 0:
                break

            pub_title_guess = pub_stub.get('bib', {}).get('title', 'Unknown Title')
            logger.info(f"Processing pub {i+1}/{num_selected}: '{pub_title_guess[:60]}...'")

            try:
                pub = None
                bib = {}
                author_str = ''
                citations = 0

                try:
                    pub = scholarly.scholarly.fill(pub_stub)
                    bib = pub.get('bib', {})
                except MaxTriesExceededException as rt_err:
                    logger.error(f"Rate limit hit while filling details for pub {i+1} ('{pub_title_guess[:50]}...'): {rt_err}. Aborting further publication processing.")
                    rate_limited = True
                    skipped_details['processing_halted_by_rate_limit'] = (num_selected - i)
                    break
                except Exception as fill_err:
                    logger.warning(f"Failed to fill details for pub {i+1} ('{pub_title_guess[:50]}...'): {fill_err}. Using stub data for checks.", exc_info=False)
                    pub = pub_stub
                    bib = pub.get('bib', {})

                title = bib.get('title', 'Title Not Available')

                author_str = bib.get('author', '')
                if not author_str:
                    logger.warning(f"Skipping pub {i+1} ('{title[:50]}...') due to missing or empty 'author' field.")
                    skipped_details['author_field_empty'] += 1
                    continue

                pub_year_str = bib.get('pub_year', None)
                if pub_year_str is None:
                    logger.warning(f"Skipping pub {i+1} ('{title[:50]}...') due to missing 'pub_year' field.")
                    skipped_details['pub_year_missing'] += 1
                    continue
                
                pub_year = 0
                try:
                    pub_year = int(pub_year_str)
                    if not (1800 <= pub_year <= current_year + 2):
                        logger.warning(f"Skipping pub {i+1} ('{title[:50]}...') due to out-of-range year: {pub_year}.")
                        skipped_details['pub_year_invalid_format_or_range'] += 1
                        continue
                except ValueError:
                    logger.warning(f"Skipping pub {i+1} ('{title[:50]}...') due to non-integer year format: '{pub_year_str}'.")
                    skipped_details['pub_year_invalid_format_or_range'] += 1
                    continue

                citations_val = pub.get('num_citations')
                if citations_val is None and pub is not pub_stub:
                    citations_val = pub_stub.get('num_citations')

                if citations_val is None:
                    citations = 0
                else:
                    citations = int(citations_val)

                num_authors_temp = count_authors(author_str)
                num_authors = 1
                if num_authors_temp is None:
                    logger.warning(f"Could not reliably count authors for pub {i+1} ('{title[:50]}...') from non-empty string '{author_str[:30]}...'. Assuming 1 author.")
                else:
                    num_authors = num_authors_temp
                
                age = max(1, current_year - pub_year + 1)
                denominator = num_authors * age
                term = citations / denominator if denominator > 0 else 0

                pub_data = {
                    'term': term, 'title': title, 'year': pub_year,
                    'citations': citations, 'authors': num_authors, 'age': age
                }
                publication_details.append(pub_data)
                preliminary_index_I += term
                processed_pubs_count += 1

                if (processed_pubs_count % 25 == 0) and processed_pubs_count > 0:
                    logger.info(f"Processed {processed_pubs_count} valid publications so far...")

            except Exception as e:
                pub_title_for_error = pub_stub.get('bib', {}).get('title', 'Unknown Title')
                logger.error(f"Critical error processing pub {i+1} ('{pub_title_for_error[:50]}...'): {e}. Skipping this publication.", exc_info=False)
                skipped_details['other_critical_error_per_pub'] += 1

        if any(skipped_details.values()):
            logger.info("--- Publication Skipping & Processing Summary ---")
            total_pubs_iterated_before_stop = i + 1 if i != -1 else 0
            
            if pubs_to_process and total_pubs_iterated_before_stop == 0 and skipped_details.get('processing_halted_by_rate_limit',0) == num_selected:
                 pass
            elif num_selected > 0 :
                 logger.info(f"Attempted to process up to publication {total_pubs_iterated_before_stop} out of {num_selected} fetched.")

            for reason, count in skipped_details.items():
                if count > 0:
                    reason_text = reason.replace('_', ' ')
                    if reason == 'processing_halted_by_rate_limit':
                        logger.warning(f"{count} publications were not processed or completed due to: {reason_text}")
                    else:
                        logger.info(f"Skipped {count} pubs (among those attempted) due to: {reason_text}")

        l_index = math.log(preliminary_index_I + 1) if preliminary_index_I > 0 else 0.0

        logger.info("Sorting processed publications by contribution score (term)...")
        sorted_contributors = sorted(publication_details, key=lambda p: p['term'], reverse=True)

        positive_term_contributors = [p for p in sorted_contributors if p['term'] > 0]
        logger.info(f"Identified {len(positive_term_contributors)} processed publications with a contribution score > 0.")

        top_contributing_list = sorted_contributors

        if rate_limited:
            logger.warning("Calculation finished BUT was affected or aborted early due to Google Scholar rate limiting.")
        else:
            logger.info(f"Calculation process completed. Processed {processed_pubs_count} publications successfully.")
            if i + 1 < num_selected and not skipped_details.get('processing_halted_by_rate_limit'):
                 logger.warning(f"Processing loop did not complete all {num_selected} fetched publications (stopped after attempting pub {i+1}). This might indicate an error not caught as rate limit, or all remaining pubs were skipped for data reasons.")

        return l_index, author_details, preliminary_index_I, processed_pubs_count, total_pubs_reported, top_contributing_list, rate_limited, skipped_details

    except Exception as e:
        logger.error(f"An unexpected critical error occurred during the main calculation process: {e}", exc_info=True)
        return None, author_details, preliminary_index_I, processed_pubs_count, total_pubs_reported, [], rate_limited, skipped_details


if __name__ == "__main__":
    print("-" * 60)
    print("L-index Calculator by Aleksey V. Belikov")
    print("-" * 60)
    max_pubs_limit = MAX_PUBS_TO_PROCESS
    print("-" * 60)
    pprint("IMPORTANT NOTES:")
    print("1. Results are entirely dependent on the accuracy, completeness and public availability of the scientist's Google Scholar profile")
    print("2. While the script attempts to find the best match for the scientist's name, errors can occur, especially for common names")
    print("3. Check the affiliation, keywords and top publications in the log or output pdf to verify that the correct scientist has been identified")
    print("4. Using the Google Scholar ID is recommended, it can be found at the end of the profile URL")
    print("5. Publications with missing author information or publication year will be skipped and a warning will be issued. Missing citation counts will be treated as 0 citations")
    print("6. If the script identifies one of the keywords for a large group of authors in the "authors" database field, it will add 50 authors to the author count, because the actual number of authors is unknown")
    print(f"7. Keywords used for this are {LARGE_GROUP_KEYWORDS}")
    print(f"8. Calculation is based on the {max_pubs_limit} of the scientist's most cited publications (or fewer if the scientist has less or some data were missing)")
    print("9. This can be changed by modifying MAX_PUBS_TO_PROCESS parameter in the code")
    print("10. Extensive requests can lead to temporary IP blocks (rate limiting) from Google Scholar, so it is recommended to keep MAX_PUBS_TO_PROCESS to 100 or below")
    print("11. It is recommended to wait (hours, or even a day) if you encounter persistent rate limiting, or try a different IP address or a proxy")
    print("12. Selecting too low a MAX_PUBS_TO_PROCESS value (e.g. <50) will lead to underestimation of the L-index")
    print("13. Nevertheless, we demonstrated that 50-100 most cited publications capture the bulk of the L-index, even for scientists with many hundreds of publications")
    print("14. Always compare scientists using the same MAX_PUBS_TO_PROCESS value to calculate their L-indices")
    print(f"15. A PDF report including the top {TOP_N_PUBS_TO_SAVE_IN_REPORT} contributing publications will be saved in the '{OUTPUT_DIR}' directory")
    print("-" * 60)

    author_query = input("Enter Author Name or Google Scholar ID: ")

    if not author_query:
        print("No author name or ID provided. Exiting.")
    else:
        l_index, author_data, prelim_I, processed_count, total_reported, top_contrib_pubs, was_rate_limited, skips_summary_data = calculate_l_index(
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
            print(f"Processing may have stopped early or been affected by Google Scholar rate limits.")
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
                 print(f"(Successfully processed {processed_count} publications before error/stop).")
                 print("Please check the script's log output for detailed error messages and skip reasons.")

            else:
                print("\n--- Results Summary ---")
                if was_rate_limited: print("(NOTE: Results based on potentially INCOMPLETE data due to rate limiting)")
                print(f"Author Identified: {author_full_name_display}")
                print(f"Affiliation:       {author_data.get('affiliation', 'N/A')}")
                print(f"Interests:         {', '.join(author_data.get('interests', [])) if author_data.get('interests') else 'N/A'}")
                scholar_id = author_data.get('scholar_id')
                print(f"Scholar Profile:   {'https://scholar.google.com/citations?user=' + scholar_id if scholar_id else 'N/A'}")
                print(f"L-Index:           {l_index:.2f}")
                print(f"Calculation Basis: {total_reported} most cited publications fetched from Google Scholar.")
                print(f"Pubs Processed:    {processed_count} / {total_reported} (Fetched)")

                total_skipped_for_data_reasons = sum(
                    count for reason, count in skips_summary_data.items()
                    if reason != 'processing_halted_by_rate_limit' and count > 0
                )
                halted_by_rate_limit_count = skips_summary_data.get('processing_halted_by_rate_limit', 0)

                if total_skipped_for_data_reasons > 0:
                    print(f"Skipped Publications (due to data issues): {total_skipped_for_data_reasons}")
                    for reason, count in skips_summary_data.items():
                        if count > 0 and reason not in ['processing_halted_by_rate_limit']:
                            print(f"      - {count} due to: {reason.replace('_', ' ')}")
                
                if halted_by_rate_limit_count > 0:
                    print(f"Processing Halted Early: {halted_by_rate_limit_count} publication(s) were not processed or completed due to rate limiting or other early stop.")


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
                            skips_summary_data
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

