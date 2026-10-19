#!/usr/bin/env python3
# coding: utf-8


import scholarly
import datetime
import math
import logging
import time
import os
import re
import requests
import urllib.parse
from difflib import SequenceMatcher
from fpdf import FPDF
from fpdf.enums import XPos, YPos, Align

# --- CAPTCHA HANDLING PATCH ---
class CaptchaException(BaseException):
    """Exception raised when Google Scholar requests a CAPTCHA."""
    pass

original_logger_info = logging.Logger.info

def custom_logger_info(self, msg, *args, **kwargs):
    if "captcha" in str(msg).lower():
        msg_str = str(msg)
        if "Got a captcha request" in msg_str or "Solving the captcha" in msg_str:
            raise CaptchaException("Google Scholar requested a CAPTCHA. Halting.")
    original_logger_info(self, msg, *args, **kwargs)

logging.Logger.info = custom_logger_info
# ------------------------------

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
logging.getLogger('fontTools').setLevel(logging.WARNING)

def sanitize_filename(name):
    s = re.sub(r'[^\w\-\.]+', '_', name)
    s = re.sub(r'_+', '_', s).strip('_')
    return s if s else "invalid_name"

def normalize_title(t):
    """Strips punctuation and spaces to make matching bulletproof."""
    return re.sub(r'[^a-z0-9]', '', str(t).lower())

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
    if ' et al' in author_string_padded: 
        additional_count += 3
        
    found_large_group = False
    for keyword in LARGE_GROUP_KEYWORDS:
        if f' {keyword} ' in author_string_padded:
            found_large_group = True
            break
            
    # --- DOUBLE COUNTING PREVENTER ---
    # If the API already returned a massive list of explicit authors (e.g., >= 50),
    # the consortium members have almost certainly been expanded. 
    # We only apply the 50 penalty if the list is small (meaning the consortium is hidden).
    if found_large_group and base_count < 50: 
        additional_count += 50
        
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
        self.set_font('DejaVu', 'B', 8)
        total_width = self.w - 2 * self.l_margin
        base_pcts = [0.04, 0.08, 0.08, 0.08, 0.05, 0.06]
        min_widths = [8, 12, 12, 12, 8, 10]
        col_widths = [max(min_w, pct * total_width) for min_w, pct in zip(min_widths, base_pcts)]
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
        line_height_for_cells = 5
        align_map = [Align.R, Align.R, Align.R, Align.R, Align.R, Align.C, Align.L]

        _avg_char_width_mm_cached = getattr(self, '_avg_char_width_mm_cached', None)
        if _avg_char_width_mm_cached is None:
            sample_str_for_avg_width = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
            _avg_char_width_mm_cached = 0
            if self.font_size > 0:
                try:
                    width_of_sample = self.get_string_width(sample_str_for_avg_width)
                    if width_of_sample > 0:
                        _avg_char_width_mm_cached = width_of_sample / len(sample_str_for_avg_width)
                except Exception: pass
            if _avg_char_width_mm_cached == 0: 
                _avg_char_width_mm_cached = self.font_size * 0.45
            if _avg_char_width_mm_cached == 0: 
                _avg_char_width_mm_cached = 1.0
            self._avg_char_width_mm_cached = _avg_char_width_mm_cached

        for row_idx, row_data in enumerate(data):
            y_start_row = self.get_y()
            max_lines_this_row = 1
            needed_row_height = 0

            try:
                for cell_idx, cell_content_text in enumerate(row_data):
                    text_for_cell = encode_string_for_pdf(cell_content_text)
                    width_for_cell = col_widths[cell_idx]
                    
                    num_cell_lines = 1
                    if width_for_cell > 0:
                        prev_x, prev_y = self.get_x(), self.get_y()
                        lines_list = self.multi_cell(
                            w=width_for_cell, h=line_height_for_cells, text=text_for_cell,
                            border=0, align=align_map[cell_idx], dry_run=True, output="LINES"
                        )
                        self.set_xy(prev_x, prev_y)
                        num_cell_lines = max(1, len(lines_list))
                    else:
                        num_cell_lines = max(1, text_for_cell.count('\n') + 1)
                    max_lines_this_row = max(max_lines_this_row, num_cell_lines)
                
                needed_row_height = max_lines_this_row * line_height_for_cells
            except (TypeError, AttributeError) as e:
                err_msg = str(e).lower()
                if 'split_only' in err_msg or "unexpected keyword argument 'split_only'" in err_msg:
                    max_estimated_lines_this_row = 1
                    for cell_idx_fallback, cell_content_text_fallback in enumerate(row_data):
                        text_fb = encode_string_for_pdf(cell_content_text_fallback)
                        width_fb = col_widths[cell_idx_fallback]
                        num_cell_lines_fb = 1
                        if width_fb > 0 and self._avg_char_width_mm_cached > 0 and len(text_fb) > 0:
                            chars_that_fit_line_fb = max(1, math.floor(width_fb / self._avg_char_width_mm_cached))
                            num_cell_lines_fb = math.ceil(len(text_fb) / chars_that_fit_line_fb)
                        num_cell_lines_fb = max(1, num_cell_lines_fb)
                        num_cell_lines_fb = max(num_cell_lines_fb, text_fb.count('\n') + 1)
                        max_estimated_lines_this_row = max(max_estimated_lines_this_row, num_cell_lines_fb)
                    needed_row_height = max_estimated_lines_this_row * line_height_for_cells
                else: raise e
            
            if y_start_row + needed_row_height > self.h - self.b_margin:
                self.add_page()
                self.set_font('DejaVu', 'B', 8)
                for i_h, title_h in enumerate(header):
                    align_val = Align.C
                    new_x_pos = XPos.RIGHT if i_h < len(header) - 1 else XPos.LMARGIN
                    new_y_pos = YPos.TOP if i_h < len(header) - 1 else YPos.NEXT
                    header_text_new = encode_string_for_pdf(title_h)
                    self.cell(col_widths[i_h], 7, header_text_new, border=1, align=align_val, new_x=new_x_pos, new_y=new_y_pos)
                self.set_font('DejaVu', '', 8)
                y_start_row = self.get_y()

            actual_row_end_y = y_start_row + needed_row_height
            current_x_for_cell = self.l_margin

            for idx, (cell_item_data, cell_w, cell_align) in enumerate(zip(row_data, col_widths, align_map)):
                self.set_xy(current_x_for_cell, y_start_row)
                processed_text = encode_string_for_pdf(cell_item_data)
                self.multi_cell(w=cell_w, h=line_height_for_cells, text=processed_text, border=0, align=cell_align, new_x=XPos.RIGHT, new_y=YPos.TOP)
                current_x_for_cell += cell_w
            
            temp_x_for_border = self.l_margin
            self.line(temp_x_for_border, y_start_row, temp_x_for_border, actual_row_end_y)
            for individual_col_width in col_widths:
                temp_x_for_border += individual_col_width
                self.line(temp_x_for_border, y_start_row, temp_x_for_border, actual_row_end_y)
            self.line(self.l_margin, actual_row_end_y, self.w - self.r_margin, actual_row_end_y)
            self.set_y(actual_row_end_y)


def save_results_to_pdf(filename, author_details, l_index, processed_count, total_pubs_reported, top_pubs, was_rate_limited, skips_summary_data):
    try:
        pdf = PDF(orientation='L', unit='mm', format='A4')
        try:
            pdf.add_font('DejaVu', '', 'DejaVuSans.ttf')
            pdf.add_font('DejaVu', 'B', 'DejaVuSans-Bold.ttf')
            pdf.add_font('DejaVu', 'I', 'DejaVuSans-Oblique.ttf')
            pdf.add_font('DejaVu', 'BI', 'DejaVuSans-BoldOblique.ttf')
        except RuntimeError as e:
            logger.error("Could not load DejaVu font. Falling back to default font.")

        pdf.add_page()
        author_name = author_details.get('name', 'N/A')
        pdf.set_font('DejaVu', 'B', 14)
        pdf.multi_cell(0, 10, encode_string_for_pdf(author_name), border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.set_font('DejaVu', '', 10)
        affiliation = author_details.get('affiliation')
        if affiliation: pdf.multi_cell(0, 5, encode_string_for_pdf(affiliation), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else: pdf.ln(1)

        interests = author_details.get('interests')
        if interests:
            pdf.multi_cell(0, 5, encode_string_for_pdf(", ".join(interests)), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else: pdf.ln(1)

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
            pdf.multi_cell(0, 5, encode_string_for_pdf("*** WARNING: Processing aborted or affected by Google Scholar rate limit (429 errors / CAPTCHA). Results may be based on incomplete data. ***"), border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0); pdf.ln(2)

        pdf.key_value("L-index", f"{l_index:.2f}" if l_index is not None else "Error")

        pdf.set_font('DejaVu', 'I', 9)
        current_date_str = datetime.datetime.now().strftime("%d %B %Y")
        pdf.multi_cell(0, 5, encode_string_for_pdf(f"Calculated on {current_date_str} based on the {total_pubs_reported} most cited publications fetched"), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font('DejaVu', '', 10)
        pdf.ln(1)

        pdf.ln(3)
        total_skipped_in_pdf = sum(count for reason, count in skips_summary_data.items() if reason != 'processing_halted_by_rate_limit' and count > 0)
        halted_early_count_pdf = skips_summary_data.get('processing_halted_by_rate_limit', 0)

        if total_skipped_in_pdf > 0 or halted_early_count_pdf > 0:
            pdf.set_font('DejaVu', 'B', 10)
            pdf.cell(0, 6, "Publication Processing Notes:", border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font('DejaVu', '', 9)

            if total_skipped_in_pdf > 0:
                pdf.multi_cell(0, 5, encode_string_for_pdf(f"- Publications skipped due to missing/invalid data: {total_skipped_in_pdf}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                for reason, count in skips_summary_data.items():
                    if count > 0 and reason != 'processing_halted_by_rate_limit':
                        pdf.multi_cell(0, 4, encode_string_for_pdf(f"    - {count} due to: {reason.replace('_', ' ')}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            
            if halted_early_count_pdf > 0:
                pdf.multi_cell(0, 5, encode_string_for_pdf(f"- Publications not processed/completed due to rate limit or early stop: {halted_early_count_pdf}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(3)

        pdf.ln(5)

        pubs_to_show = top_pubs[:TOP_N_PUBS_TO_SAVE_IN_REPORT]
        pdf.chapter_title(f"Top {len(pubs_to_show)} Contributing Publications (among {processed_count} successfully processed)")

        if not pubs_to_show:
            pdf.cell(0, 6, "(No publications processed had a contribution score > 0 or processing was stopped/encountered issues)", border=0, align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            header = ['#', 'Score', 'Cites', 'Authors', 'Age', 'Year', 'Title']
            table_data = []
            for i, pub_data in enumerate(pubs_to_show):
                title_str = str(pub_data['title'])[:150] + ('...' if len(str(pub_data['title'])) > 150 else '')
                table_data.append([f"{i+1}.", f"{pub_data['term']:.1f}", str(pub_data['citations']), str(pub_data['authors']), str(pub_data['age']), str(pub_data['year']), title_str])
            pdf.publication_table(header, table_data)

        pdf.ln(10)
        pdf.set_font('DejaVu','', 8)
        pdf.cell(0, 5, encode_string_for_pdf("L-index Calculator by Aleksey V. Belikov, 2025"), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.cell(0, 5, encode_string_for_pdf("L-index concept by Aleksey V. Belikov & Vitaly V. Belikov, 2015"), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(5)
        citation_url = "https://doi.org/10.12688/f1000research.7070.2"
        pdf.multi_cell(0, 4, encode_string_for_pdf("Belikov AV and Belikov VV. A citation-based, author- and age-normalized, logarithmic index for evaluation of individual researchers independently of publication counts. F1000Research 2015, 4:884"), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 255); pdf.set_font('', 'U')
        pdf.cell(0, 4, encode_string_for_pdf(f"({citation_url})"), align='L', link=citation_url, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0); pdf.set_font('', '')

        pdf.output(filename)
        logger.info(f"Results successfully saved to PDF: {filename}")
    except Exception as e:
        logger.error(f"Failed to generate PDF report: {e}", exc_info=True)


def calculate_l_index(author_name_or_id, max_pubs_limit):
    preliminary_index_I = 0.0
    processed_pubs_count = 0
    author_details = {'name': 'N/A', 'affiliation': None, 'interests': [], 'scholar_id': None, 'citedby': 'N/A'}
    publication_details = []
    rate_limited = False
    total_pubs_reported = 0
    i = -1
    skipped_details = {'author_field_empty': 0, 'pub_year_missing': 0, 'pub_year_invalid_format_or_range': 0, 'processing_halted_by_rate_limit': 0, 'other_critical_error_per_pub': 0}

    try:
        logger.info(f"Searching for author: {author_name_or_id}")
        is_id_search = bool(re.match(r'^[\w-]{12}$', author_name_or_id))
        author_to_process = None

        if is_id_search:
            try:
                author_stub = scholarly.scholarly.search_author_id(author_name_or_id, filled=False)
                if not author_stub: raise ValueError("No author found")
                author_details['scholar_id'] = author_stub.get('scholar_id')
                author_details['name'] = author_stub.get('name', 'Name Not Found')
                author_to_process = author_stub
                logger.info(f"Found author by ID: {author_details['name']} (ID: {author_details['scholar_id']})")
            except (MaxTriesExceededException, CaptchaException) as rt_err: 
                logger.error(f"Rate limit during author ID lookup: {rt_err}"); rate_limited = True; return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details
            except Exception as e: 
                logger.error(f"Failed during author ID lookup: {e}"); return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details
        else:
             potential_authors = []
             try:
                  search_query = scholarly.scholarly.search_author(author_name_or_id)
                  for idx in range(MAX_SEARCH_RESULTS_TO_CHECK):
                     try:
                         auth = next(search_query, None)
                         if auth is None: break
                         if auth and 'scholar_id' in auth: potential_authors.append(auth)
                     except StopIteration: break
                     except (MaxTriesExceededException, CaptchaException) as rt_err_inner: 
                         logger.error(f"Rate limit during author search: {rt_err_inner}. Stopping search."); rate_limited = True; break
                  logger.info(f"Found {len(potential_authors)} potential author(s) with IDs.")
             except (MaxTriesExceededException, CaptchaException) as rt_err: 
                 logger.error(f"Rate limit during author search setup: {rt_err}"); rate_limited = True
             except Exception as e: logger.error(f"Error during author search setup: {e}"); potential_authors = []

             if rate_limited: return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details
             if not potential_authors: logger.error(f"Author not found."); return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details

             best_match_author = None; highest_ratio = 0.0; query_lower = author_name_or_id.lower()
             for pa in potential_authors:
                 name_lower = pa.get('name', '').lower()
                 if not name_lower: continue
                 ratio = SequenceMatcher(None, query_lower, name_lower).ratio()
                 if ratio > highest_ratio:
                     highest_ratio = ratio
                     best_match_author = pa

             effective_threshold = NAME_SIMILARITY_THRESHOLD if len(potential_authors) > 1 else SINGLE_RESULT_SIMILARITY_THRESHOLD
             if best_match_author and highest_ratio >= effective_threshold:
                 author_to_process = best_match_author
                 author_details['scholar_id'] = author_to_process.get('scholar_id')
                 author_details['name'] = author_to_process.get('name', 'Name Not Found')
             else:
                 logger.error(f"Failed to identify a sufficiently similar author match.")
                 return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details

        logger.info(f"Fetching full profile details for {author_details.get('name')}...")
        try:
            author_filled_profile = scholarly.scholarly.fill(author_to_process, sections=['basics', 'indices', 'interests', 'coauthors', 'counts'])
            author_details['name'] = author_filled_profile.get('name', author_details.get('name'))
            author_details['affiliation'] = author_filled_profile.get('affiliation')
            author_details['interests'] = author_filled_profile.get('interests', [])
            author_details['citedby'] = author_filled_profile.get('citedby', 'N/A')
            logger.info(f"Successfully fetched profile details. Total citations: {author_details['citedby']}")
        except (MaxTriesExceededException, CaptchaException) as rt_err:
            logger.error(f"Rate limit occurred while fetching profile: {rt_err}"); rate_limited = True
        except Exception as e:
            logger.error(f"Error filling author profile: {e}")

        logger.info(f"Fetching initial publication list (limit {max_pubs_limit})...")
        initial_pubs = []
        try:
            author_obj_for_pubs = author_filled_profile if author_filled_profile else author_to_process
            if 'publications' not in author_obj_for_pubs:
                author_pubs_filled = scholarly.scholarly.fill(author_obj_for_pubs, sections=['publications'], sortby='citedby', publication_limit=max_pubs_limit)
            else: author_pubs_filled = author_obj_for_pubs
            initial_pubs = author_pubs_filled.get('publications', [])[:max_pubs_limit] if author_pubs_filled else []
        except (MaxTriesExceededException, CaptchaException) as rt_err:
            logger.error(f"Rate limit occurred fetching publications: {rt_err}"); return None, author_details, 0.0, 0, 0, [], True, skipped_details
        except Exception as e:
            logger.error(f"Error fetching publication list: {e}"); return None, author_details, 0.0, 0, 0, [], rate_limited, skipped_details

        total_pubs_reported = len(initial_pubs)
        if not initial_pubs and not rate_limited:
            return 0.0, author_details, 0.0, 0, total_pubs_reported, [], rate_limited, skipped_details

        pubs_to_process = initial_pubs
        num_selected = len(pubs_to_process)
        logger.info(f"Fetched {num_selected} publications. Starting processing...")
        current_year = datetime.datetime.now().year

        for i, pub_stub in enumerate(pubs_to_process):
            if rate_limited and not skipped_details['processing_halted_by_rate_limit']:
                skipped_details['processing_halted_by_rate_limit'] = num_selected - i
                logger.warning(f"Skipping remaining {num_selected - i} publications due to rate limit.")
                break
            if skipped_details['processing_halted_by_rate_limit'] > 0: break

            pub_title_guess = pub_stub.get('bib', {}).get('title', 'Unknown Title')
            logger.info(f"Processing pub {i+1}/{num_selected}: '{pub_title_guess[:60]}...'")

            try:
                # --- API CASCADE OPTIMIZATION ---
                stub_bib = pub_stub.get('bib', {})
                stub_author = stub_bib.get('author', '')
                stub_year = stub_bib.get('pub_year')

                # Create a local dictionary we can safely update
                bib = dict(stub_bib)
                pub = pub_stub

                needs_fill = False
                if not stub_author or '...' in stub_author or '…' in stub_author:
                    needs_fill = True
                elif not stub_year:
                    needs_fill = True

                title = bib.get('title', 'Title Not Available')

                if needs_fill and title != 'Title Not Available':
                    logger.info("  -> Data truncated. Attempting API lookups to avoid Google Scholar limits...")
                    
                    # Clean out truncation markers to vastly improve API search match accuracy
                    search_title = title.replace("...", "").replace("…", "").strip()
                    safe_title = urllib.parse.quote(search_title)
                    gs_title_norm = normalize_title(search_title)
                    compare_len = len(gs_title_norm)

                    # 1. Try Semantic Scholar API (Fastest, High Hit Rate)
                    if needs_fill:
                        try:
                            sem_url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={safe_title}&fields=title,authors,year&limit=3"
                            resp = requests.get(sem_url, timeout=5)
                            if resp.status_code == 200:
                                data = resp.json()
                                for item in data.get('data', []):
                                    sem_title = item.get('title', '')
                                    # Compare against the exact alphanumeric footprint of the Scholar title
                                    if SequenceMatcher(None, gs_title_norm, normalize_title(sem_title)[:compare_len]).ratio() > 0.85:
                                        sem_authors = item.get('authors', [])
                                        author_names = [a.get('name', '') for a in sem_authors if a.get('name')]
                                        if author_names:
                                            bib['author'] = " and ".join(author_names)
                                            if not stub_year and item.get('year'):
                                                bib['pub_year'] = str(item.get('year'))
                                            logger.info(f"  -> Semantic Scholar success! Found {len(author_names)} authors.")
                                            needs_fill = False
                                            break
                        except Exception as e:
                            pass
                            
                    # 2. Try Crossref API (Official DOI Registry)
                    if needs_fill:
                        try:
                            author_name_str = author_details.get('name', '')
                            author_last = author_name_str.split()[-1] if author_name_str else ""
                            
                            cr_url = f"https://api.crossref.org/works?query.bibliographic={safe_title}"
                            if author_last: cr_url += f"&query.author={urllib.parse.quote(author_last)}"
                            cr_url += "&select=author,title,published-print,published-online,issued&rows=5"
                            
                            headers = {'User-Agent': 'L-indexCalculator/1.3 (mailto:aleksey.belikov@example.com)'}
                            resp = requests.get(cr_url, headers=headers, timeout=5)
                            if resp.status_code == 200:
                                data = resp.json()
                                for item in data.get('message', {}).get('items', []):
                                    cr_titles = item.get('title', [])
                                    if cr_titles:
                                        cr_title = str(cr_titles[0])
                                        if SequenceMatcher(None, gs_title_norm, normalize_title(cr_title)[:compare_len]).ratio() > 0.85:
                                            cr_authors = item.get('author', [])
                                            author_names = []
                                            for a in cr_authors:
                                                if 'family' in a:
                                                    given = a.get('given', '')
                                                    family = a.get('family', '')
                                                    if given or family:
                                                        author_names.append(f"{given} {family}".strip())
                                                elif 'name' in a:
                                                    author_names.append(str(a.get('name', '')).strip())
                                            if author_names:
                                                bib['author'] = " and ".join(author_names)
                                                if not stub_year:
                                                    date_parts = item.get('published-print', {}).get('date-parts', [])
                                                    if not date_parts: date_parts = item.get('published-online', {}).get('date-parts', [])
                                                    if not date_parts: date_parts = item.get('issued', {}).get('date-parts', [])
                                                    if date_parts and len(date_parts) > 0 and len(date_parts[0]) > 0:
                                                        bib['pub_year'] = str(date_parts[0][0])
                                                logger.info(f"  -> Crossref success! Found {len(author_names)} authors.")
                                                needs_fill = False
                                                break
                        except Exception as e:
                            pass

                # 3. Fallback to Google Scholar detailed page
                if needs_fill:
                    logger.info("  -> APIs missed. Falling back to Google Scholar detailed page...")
                    try:
                        time.sleep(2.5) # generous delay to survive GS limits if we must hit them
                        pub = scholarly.scholarly.fill(pub_stub)
                        bib = pub.get('bib', {})
                    except (MaxTriesExceededException, CaptchaException) as rt_err:
                        logger.error(f"Rate limit hit while filling details for pub {i+1}: {rt_err}")
                        rate_limited = True
                        skipped_details['processing_halted_by_rate_limit'] = (num_selected - i)
                        break
                    except Exception as fill_err:
                        logger.warning(f"Failed to fill details for pub {i+1}: {fill_err}. Using stub data.")
                        pub = pub_stub
                        bib = pub.get('bib', {})
                # --- OPTIMIZATION END ---

                author_str = bib.get('author', '')
                if not author_str:
                    logger.warning(f"Skipping pub {i+1} due to missing 'author' field.")
                    skipped_details['author_field_empty'] += 1
                    continue

                pub_year_str = bib.get('pub_year', None)
                if pub_year_str is None:
                    logger.warning(f"Skipping pub {i+1} due to missing 'pub_year' field.")
                    skipped_details['pub_year_missing'] += 1
                    continue
                
                try:
                    pub_year = int(pub_year_str)
                    if not (1800 <= pub_year <= current_year + 2):
                        logger.warning(f"Skipping pub {i+1} due to out-of-range year: {pub_year}.")
                        skipped_details['pub_year_invalid_format_or_range'] += 1
                        continue
                except ValueError:
                    logger.warning(f"Skipping pub {i+1} due to non-integer year format: '{pub_year_str}'.")
                    skipped_details['pub_year_invalid_format_or_range'] += 1
                    continue

                citations_val = pub.get('num_citations')
                if citations_val is None and pub is not pub_stub:
                    citations_val = pub_stub.get('num_citations')

                citations = int(citations_val) if citations_val is not None else 0

                num_authors_temp = count_authors(author_str)
                num_authors = num_authors_temp if num_authors_temp is not None else 1
                
                age = max(1, current_year - pub_year + 1)
                denominator = num_authors * age
                term = citations / denominator if denominator > 0 else 0

                publication_details.append({
                    'term': term, 'title': title, 'year': pub_year,
                    'citations': citations, 'authors': num_authors, 'age': age
                })
                preliminary_index_I += term
                processed_pubs_count += 1

                if (processed_pubs_count % 25 == 0) and processed_pubs_count > 0:
                    logger.info(f"Processed {processed_pubs_count} valid publications so far...")

            except Exception as e:
                logger.error(f"Critical error processing pub {i+1}: {e}")
                skipped_details['other_critical_error_per_pub'] += 1

        if any(skipped_details.values()):
            logger.info("--- Publication Skipping & Processing Summary ---")
            for reason, count in skipped_details.items():
                if count > 0: logger.info(f"[{reason}]: {count}")

        l_index = math.log(preliminary_index_I + 1) if preliminary_index_I > 0 else 0.0
        sorted_contributors = sorted(publication_details, key=lambda p: p['term'], reverse=True)
        top_contributing_list = sorted_contributors

        if rate_limited: logger.warning("Calculation affected by Google Scholar rate limiting.")
        else: logger.info(f"Process completed. Processed {processed_pubs_count} publications.")

        return l_index, author_details, preliminary_index_I, processed_pubs_count, total_pubs_reported, top_contributing_list, rate_limited, skipped_details

    except Exception as e:
        logger.error(f"Unexpected critical error: {e}", exc_info=True)
        return None, author_details, preliminary_index_I, processed_pubs_count, total_pubs_reported, [], rate_limited, skipped_details

if __name__ == "__main__":
    print("-" * 60)
    print("L-index Calculator by Aleksey V. Belikov")
    print("-" * 60)
    max_pubs_limit = MAX_PUBS_TO_PROCESS
    print("-" * 60)
    print("1. Results are entirely dependent on the accuracy, completeness and public availability of the scientist's Google Scholar profile")
    print("2. A scientist's Google Scholar ID can be found at the end of their profile URL")
    print("3. Publications with missing author information or publication year will be skipped and a warning will be issued. Missing citation counts will be treated as 0 citations")
    print("4. If the script identifies one of the keywords for a large group of authors in the authors database field, it will add 50 authors to the author count, because the actual number of authors is unknown")
    print(f"5. Keywords used for this are {LARGE_GROUP_KEYWORDS}")
    print(f"6. Calculation is based on the {max_pubs_limit} of the scientist's most cited publications (or fewer if the scientist has less or some data were missing)")
    print("7. This can be changed by modifying MAX_PUBS_TO_PROCESS parameter in the code")
    print("8. Extensive requests can lead to temporary IP blocks (rate limiting) from Google Scholar, so it is recommended to keep MAX_PUBS_TO_PROCESS to 100 or below")
    print("9. It is recommended to wait (hours, or even a day) if you encounter persistent rate limiting, or try a different IP address or a proxy")
    print("10. Selecting too low a MAX_PUBS_TO_PROCESS value (e.g. <50) will lead to underestimation of the L-index")
    print("11. Nevertheless, we demonstrated that 50-100 most cited publications capture the bulk of the L-index, even for scientists with many hundreds of publications")
    print("12. Always compare scientists using the same MAX_PUBS_TO_PROCESS value to calculate their L-indices")
    print("-" * 60)

    author_query = input("Enter the scientist's Google Scholar ID: ")

    if not author_query:
        print("No author name or ID provided. Exiting.")
    else:
        l_index, author_data, prelim_I, processed_count, total_reported, top_contrib_pubs, was_rate_limited, skips_summary_data = calculate_l_index(author_query, max_pubs_limit)

        author_full_name = author_data.get('name', 'N/A')

        if was_rate_limited:
            print("\n--- WARNING: RATE LIMITED ---")
            print("Results might be based on INCOMPLETE data gathered before a limit was hit.")
            print("-" * 60)

        if author_data.get('scholar_id'):
            if l_index is None:
                 print("\n--- Calculation Error ---")
                 print(f"Author Identified: {author_full_name} (ID: {author_data.get('scholar_id', 'N/A')})")
                 print("Could not complete L-index calculation. Check the logs.")
            else:
                print("\n--- Results Summary ---")
                if was_rate_limited: print("(NOTE: Results based on potentially INCOMPLETE data)")
                print(f"Author Identified: {author_full_name}")
                print(f"Affiliation:       {author_data.get('affiliation', 'N/A')}")
                scholar_id = author_data.get('scholar_id')
                print(f"Scholar Profile:   {'https://scholar.google.com/citations?user=' + scholar_id if scholar_id else 'N/A'}")
                print(f"L-Index:           {l_index:.2f}")
                print(f"Pubs Processed:    {processed_count} / {total_reported} (Fetched)")

                if author_full_name and author_full_name != 'N/A':
                    try:
                        safe_filename_base = sanitize_filename(f"{author_full_name}_{author_data.get('scholar_id', 'NoID')}")
                        status_tag = "_RATE_LIMITED" if was_rate_limited else ""
                        date_str = datetime.date.today().isoformat()
                        pdf_filename = os.path.join(OUTPUT_DIR, f"{safe_filename_base}_L-Index_BasedOn{max_pubs_limit}{status_tag}_{date_str}.pdf")
                        os.makedirs(OUTPUT_DIR, exist_ok=True)
                        save_results_to_pdf(pdf_filename, author_data, l_index, processed_count, total_reported, top_contrib_pubs, was_rate_limited, skips_summary_data)
                    except Exception: pass
        else:
             print("\n--- Author Not Found ---")
             print("Could not calculate L-index.")

        print("-" * 60)