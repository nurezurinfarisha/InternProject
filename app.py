from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash, Response
import mysql.connector
import time
import requests
import csv
import logging
import os
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
from flask_session import Session

app = Flask(__name__)
app.secret_key = '187b09147f02bec0feaadd6e54e8c780'
ADMIN_PASSWORD = 'admin123'  # This is a simple password, you can change it to something stronger

# Set up logging to print activity details
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logging.basicConfig(filename='papers_insertion.log', level=logging.INFO,
                    format='%(asctime)s - %(message)s')
                    
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

# === ✅ Correct MySQL Connection ===
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",
    database="scopus_db"
)

# === 🔍 Scopus API Config ===
api_key = "af632813f032e8279a40f4cc7084f7b5"
SCOPUS_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
SCOPUS_ABSTRACT_URL = "https://api.elsevier.com/content/abstract/scopus_id/"
SCOPUS_AFFILIATION_URL = "https://api.elsevier.com/content/affiliation/"


def exponential_backoff(retries):
    # Use exponential backoff strategy (e.g., 1s, 2s, 4s, 8s, etc.)
    wait_time = 2 ** retries  # Exponential backoff
    logging.warning(f"⚠️ Retrying in {wait_time} seconds...")
    time.sleep(wait_time)

def load_lecturer_data():
    lecturers = set()
    
    # Query the database for indexed names
    cursor = db.cursor(dictionary=True)  # Use dictionary=True to fetch rows as dictionaries
    cursor.execute("SELECT `indexed_name` FROM lecturer")
    rows = cursor.fetchall()
    cursor.close()

    # Add indexed names to the set
    for row in rows:
        indexed = row['indexed_name'].strip().lower()  # Now row is a dictionary
        if indexed:
            lecturers.add(indexed)

    return lecturers

lecturers = load_lecturer_data()


def categorize_author(author_name, lecturers):
    author_name = author_name.strip().lower()
    return "Staff" if author_name in lecturers else "Student"

def categorize_authors_in_paper(paper, lecturers):
    paper = paper.copy()
    paper['authors_category'] = []  # Ensure this is always initialized

    authors = paper.get('Authors')
    if not authors:
        return paper  # Skip if no author info

    for author in authors.split(", "):
        if author.strip() in ("", "N/A"):
            continue
        category = categorize_author(author, lecturers)
        paper['authors_category'].append(category)

    # If no authors are categorized, add a default category
    if not paper['authors_category']:
        paper['authors_category'] = ['Unknown']

    return paper

@app.route('/filter_by_author_type/<string:author_type>')
def filter_by_author_type(author_type):
    scopus_ids = session.get('scopus_ids', [])
    if not scopus_ids:
        flash("No search results to filter. Please search first.", "warning")
        return redirect(url_for('index'))

    cursor = db.cursor(dictionary=True)
    format_strings = ','.join(['%s'] * len(scopus_ids))
    cursor.execute(f"SELECT * FROM papers WHERE SCOPUS_ID IN ({format_strings})", tuple(scopus_ids))
    papers = cursor.fetchall()
    cursor.close()

    categorized_papers = []
    for paper in papers:
        categorized_paper = categorize_authors_in_paper(paper, lecturers)
        authors_with_category = list(zip(
            categorized_paper['Authors'].split(', '),
            categorized_paper['authors_category']
        ))
        categorized_paper['authors_with_category'] = authors_with_category
        categorized_papers.append(categorized_paper)

    # ✅ Count BEFORE filtering
    staff_count = sum("Staff" in p['authors_category'] for p in categorized_papers)
    student_count = sum("Student" in p['authors_category'] for p in categorized_papers)

    # Filter
    if author_type == "staff":
        filtered = [p for p in categorized_papers if "Staff" in p['authors_category']]
        for p in filtered:
            p['display_authors'] = [name for name, _ in p['authors_with_category']]

    elif author_type == "student":
        filtered = [p for p in categorized_papers if all(cat == "Student" for cat in p['authors_category'])]
        for p in filtered:
            p['display_authors'] = [name for name, cat in p['authors_with_category'] if cat == "Student"]

    else:
        filtered = categorized_papers
        for p in filtered:
            p['display_authors'] = [name for name, _ in p['authors_with_category']]

    total_results = len(filtered)
    page = 1
    per_page = 10
    total_pages = (total_results // per_page) + (1 if total_results % per_page > 0 else 0)

    return render_template('result.html',
        results=filtered,
        query=session.get('query', ''),
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        sort_by='newest',
        staff_count=staff_count,
        student_count=student_count,
        total_results=total_results
    )


@app.route('/')
def index():
    return render_template('index.html')

def get_affiliation_id(affiliation_name):
    url = "https://api.elsevier.com/content/search/affiliation"
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    params = {"query": f"affil({affiliation_name})", "count": 1}

    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        results = response.json().get("search-results", {}).get("entry", [])
        if results:
            return results[0].get("dc:identifier", "").split(":")[-1]  # Get ID part
    return None


# Example logging in the Scopus data fetching function
def get_scopus_data(query, api_key):
    all_entries = []
    start = 0
    count = 50
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}

    # Initial request to get total results
    params = {"query": query, "count": count, "start": start}
    logging.info(f"🔍 Sending request to Scopus API with query: {query} | start={start} count={count}")
    response = requests.get(SCOPUS_SEARCH_URL, headers=headers, params=params)

    if response.status_code != 200:
        logging.error(f"⚠️ API Error {response.status_code}: {response.text}")
        return {"search-results": {"entry": []}}

    data = response.json()
    total_results = int(data.get("search-results", {}).get("opensearch:totalResults", 0))
    logging.info(f"📦 Total results to fetch: {total_results}")

    if total_results == 0:
        return {"search-results": {"entry": []}, "no_results": True}

    entries = data.get("search-results", {}).get("entry", [])
    all_entries.extend(entries)

    # Fetch the rest
    while len(all_entries) < total_results:
        start += count
        params["start"] = start
        logging.info(f"🔄 Fetching more data from Scopus API: start={start}, count={count}")
        response = requests.get(SCOPUS_SEARCH_URL, headers=headers, params=params)
        if response.status_code != 200:
            logging.error(f"⚠️ Failed at start={start}, status={response.status_code}")
            break

        entries = response.json().get("search-results", {}).get("entry", [])
        if not entries:
            logging.info("✅ No more results found.")
            break

        all_entries.extend(entries)
        logging.info(f"🔄 Fetched {len(all_entries)} of {total_results}...")

        time.sleep(1)  # Avoid rate-limiting issues

    logging.info(f"✅ Fetched a total of {len(all_entries)} entries.")
    return {"search-results": {"entry": all_entries}}


def get_abstract(scopus_id, api_key):
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    params = {"view": "FULL"}

    for attempt in range(3):  # Retry 3 times with exponential backoff
        try:
            response = requests.get(SCOPUS_ABSTRACT_URL + scopus_id, headers=headers, params=params)
            if response.status_code == 200:
                coredata = response.json().get("abstracts-retrieval-response", {}).get("coredata", {})
                abstract = coredata.get("dc:description")
                if not abstract:
                    logging.warning(f"⚠️ No abstract found for SCOPUS_ID {scopus_id}")
                    return "N/A"
                return abstract
            elif response.status_code == 429:  # Too Many Requests (rate limit reached)
                exponential_backoff(attempt)
            else:
                logging.warning(f"⚠️ Failed to fetch abstract for {scopus_id} | Status: {response.status_code}")
                break
        except Exception as e:
            logging.error(f"❌ Error during abstract fetch for {scopus_id}: {e}")
            exponential_backoff(attempt)

    return "N/A"



def get_subject_classification(scopus_id, api_key):
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    params = {"view": "FULL"}

    for attempt in range(3):
        try:
            response = requests.get(SCOPUS_ABSTRACT_URL + scopus_id, headers=headers, params=params)
            if response.status_code == 200:
                subjects = response.json().get("abstracts-retrieval-response", {}).get("subject-areas", {}).get("subject-area", [])
                if not subjects:
                    logging.warning(f"⚠️ No subject classification found for SCOPUS_ID {scopus_id}")
                    return "N/A"

                if isinstance(subjects, list):
                    return ", ".join(s.get("$", "N/A") for s in subjects)
                elif isinstance(subjects, dict):
                    return subjects.get("$", "N/A")
            else:
                logging.warning(f"⚠️ Attempt {attempt+1}: Failed subject classification for {scopus_id} | Status: {response.status_code}")
        except Exception as e:
            logging.error(f"❌ Error during subject classification fetch for {scopus_id}: {e}")
        time.sleep(2 ** attempt)

    return "N/A"

def list_to_str(value):
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    elif value is None:
        return ""
    return str(value)


def get_authors(scopus_id, api_key):
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    params = {"view": "FULL"}

    for attempt in range(3):  # Retry 3 times with exponential backoff
        try:
            response = requests.get(SCOPUS_ABSTRACT_URL + scopus_id, headers=headers, params=params)
            if response.status_code == 200:
                data = response.json().get("abstracts-retrieval-response", {})
                authors_data = data.get("authors", {}).get("author", [])
                if not authors_data:
                    logging.warning(f"⚠️ No author data found for SCOPUS_ID {scopus_id}")
                    return [], [], []

                names, ids, affiliations = [], [], []
                for author in authors_data:
                    names.append(author.get("ce:indexed-name", "N/A"))
                    ids.append(author.get("@auid", "N/A"))
                    affils = author.get("affiliation", [])
                    if isinstance(affils, list):
                        aff_ids = [a.get("@id", "N/A") for a in affils]
                    else:
                        aff_ids = [affils.get("@id", "N/A")]
                    affiliations.append("; ".join(aff_ids))

                return names, ids, affiliations
            elif response.status_code == 429:  # Rate limit error
                exponential_backoff(attempt)
            else:
                logging.warning(f"⚠️ Failed to fetch authors for {scopus_id} | Status: {response.status_code}")
                break
        except Exception as e:
            logging.error(f"❌ Error during author fetch for {scopus_id}: {e}")
            exponential_backoff(attempt)

    return [], [], []

def extract_paper_details(data, api_key):
    papers = []
    if not data or "search-results" not in data:
        return papers

    for entry in data["search-results"].get("entry", []):
        scopus_id = entry.get("dc:identifier", "").split(":")[-1]
        authors, author_ids, author_affiliations = get_authors(scopus_id, api_key)
        abstract = get_abstract(scopus_id, api_key)
        subjects = get_subject_classification(scopus_id, api_key)

        affiliations = entry.get("affiliation", [])
        affil_names, affil_cities, affil_countries = [], [], []
        if isinstance(affiliations, list):
            for affil in affiliations:
                affil_names.append(affil.get("affilname", "N/A"))
                affil_cities.append(affil.get("affiliation-city", "N/A"))
                affil_countries.append(affil.get("affiliation-country", "N/A"))
        elif isinstance(affiliations, dict):  # Sometimes only one affiliation
            affil_names.append(affiliations.get("affilname", "N/A"))
            affil_cities.append(affiliations.get("affiliation-city", "N/A"))
            affil_countries.append(affiliations.get("affiliation-country", "N/A"))
        else:
            affil_names.append("N/A")
            affil_cities.append("N/A")
            affil_countries.append("N/A")

        papers.append({
            "SCOPUS_ID": scopus_id,
            "EID": entry.get("eid", "N/A"),
            "Title": entry.get("dc:title", "N/A"),
            "Authors": authors,
            "Author IDs": author_ids,
            "Author Affiliations": author_affiliations,
            "Creators": entry.get("dc:creator", "N/A"),
            "Publication Name": entry.get("prism:publicationName", "N/A"),
            "eISSN": entry.get("prism:eIssn", "N/A"),
            "Volume": entry.get("prism:volume", "N/A"),
            "Page Range": entry.get("prism:pageRange", "N/A"),
            "Cover Date": entry.get("prism:coverDate", "N/A"),
            "DOI": entry.get("prism:doi", "N/A"),
            "Cited By Count": entry.get("citedby-count", "N/A"),
            "Aggregation Type": entry.get("prism:aggregationType", "N/A"),
            "Subtype": entry.get("subtype", "N/A"),
            "Subtype Description": entry.get("subtypeDescription", "N/A"),
            "Source ID": entry.get("source-id", "N/A"),
            "Open Access": entry.get("openaccess", "N/A"),
            "URL": entry.get("prism:url", "N/A"),
            "Abstract": abstract,
            "Subject Classification": subjects,
            "Affiliation Names": " | ".join(str(name or "N/A") for name in affil_names),
            "Affiliation Cities": " | ".join(str(city or "N/A") for city in affil_cities),
            "Affiliation Countries": " | ".join(str(country or "N/A") for country in affil_countries),
        })

        time.sleep(1)
    return papers

def safe(value):
    return value if value not in [None, ""] else "N/A"

def find_affiliation_in_db(affiliation, valid_aff_ids):
    """
    This function checks if any of the given affiliation IDs exist in the database.
    If found, returns the matching affiliation ID, otherwise returns None.
    """
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT `Author Affiliations` FROM papers")
    rows = cursor.fetchall()
    
    # Search for any of the valid affiliation IDs in the database
    for row in rows:
        aff_ids = [i.strip() for i in (row['Author Affiliations'] or '').split(",")]
        if any(aff_id in aff_ids for aff_id in valid_aff_ids):
            matched_aff_id = next(aff_id for aff_id in valid_aff_ids if aff_id in aff_ids)
            cursor.close()
            return matched_aff_id
    
    cursor.close()
    return None

@app.route('/search', methods=['POST'])
def search():
    query = request.form.get('query')
    filters = []

    # User input filters
    author = request.form.get('author_value')
    affiliation = request.form.get('affiliations_value')
    year = request.form.get('publication_year_value')
    scopus_id = request.form.get('scopus_id_value')
    selected_filters = request.form.getlist('filter[]')

    if not query.strip() and not any([author, affiliation, year, scopus_id] + selected_filters):
        flash("Please insert a query or select a filter to proceed.", "warning")
        return redirect(url_for('index'))  # Redirect back to the index page

    query_clean = query.strip().lower() if query else ''
    query_search = f"%{query_clean}%"

    # Build Scopus query
    if query_clean:
        query = f'TITLE("{query_clean}")'
    else:
        query = ''

    if 'author' in selected_filters and author:
        filters.append(f'AUTH("{author}")')

    # Check for multiple affiliation IDs in the database first if UUM (or similar) is selected
    matched_aff_id = None
    if 'affiliations' in selected_filters and affiliation:
        affiliation_lower = affiliation.strip().lower()

        # Define the list of valid affiliation IDs (for example, UUM-related IDs)
        valid_aff_ids = ["60002763", "60212344", "60228314", "60228313", "60212346"]

        # Check if the affiliation corresponds to UUM or a similar condition
        if affiliation_lower == "uum" or affiliation_lower == "university utara malaysia" or affiliation_lower == "universiti utara malaysia":
            # Use the new function to check the database for multiple IDs
            matched_aff_id = find_affiliation_in_db(affiliation, valid_aff_ids)

            # If no match is found in DB, fall back to Scopus API for "60002763"
            if not matched_aff_id:
                logging.info(f"No match in DB for '{affiliation}'. Querying Scopus for ID 60002763.")
                aff_id = get_affiliation_id("60002763")  # Only query Scopus for this ID
                if aff_id:
                    filters.append(f'AF-ID({aff_id})')
                    matched_aff_id = aff_id
                else:
                    logging.warning(f"⚠️ No affiliation ID found for: {affiliation}")

            # If a matched affiliation ID is found, add to filters
            if matched_aff_id:
                filters.append(f'AF-ID({matched_aff_id})')

    if 'publication_year' in selected_filters and year:
        filters.append(f'PUBYEAR IS {year}')
    if 'scopus_id' in selected_filters and scopus_id:
        filters.append(f'SCOPUS_ID:{scopus_id}')

    if filters:
        if query:
            query += " AND " + " AND ".join(filters)
        else:
            query = " AND ".join(filters)

    logging.info(f"Search Query: {query}")

    # === Search Local DB ===
    cursor = db.cursor(dictionary=True)
    sql = "SELECT * FROM papers WHERE LOWER(TRIM(Title)) LIKE %s"
    params = [query_search]

    if 'author' in selected_filters and author:
        sql += " AND LOWER(Authors) LIKE %s"
        params.append(f"%{author.lower()}%")
    if matched_aff_id:
    # Check against all valid_aff_ids in DB
        aff_conditions = " OR ".join(["`Author Affiliations` LIKE %s" for _ in valid_aff_ids])
        sql += f" AND ({aff_conditions})"
        params.extend([f"%{aff_id}%" for aff_id in valid_aff_ids])
    if 'publication_year' in selected_filters and year:
        sql += " AND `Cover Date` LIKE %s"
        params.append(f"{year}%")
    if 'scopus_id' in selected_filters and scopus_id:
        sql += " AND SCOPUS_ID = %s"
        params.append(scopus_id)

    cursor.execute(sql, tuple(params))
    existing_papers = cursor.fetchall()
    cursor.close()

    if existing_papers:
        logging.info("🔍 Found results in database for query with filters.")
        flash("Data fetched successfully for selected", "success")

        categorized_papers = []
        staff_count = 0
        student_count = 0

        for paper in existing_papers:
            paper = categorize_authors_in_paper(paper, lecturers)
            categorized_papers.append(paper)
            if "Staff" in paper["authors_category"]:
                staff_count += 1
            else:
                student_count += 1

        session['scopus_ids'] = [paper['SCOPUS_ID'] for paper in categorized_papers]
        session['query'] = query
        session.modified = True

        # Redirect to result page after flash message
        return redirect(url_for('result', page=1, sort_by='newest'))

    # === Scopus Fallback ===
    logging.info(f"🚀 Data not found in database. Querying Scopus API for: {query}")
    scopus_data = get_scopus_data(query, api_key)
    paper_details = extract_paper_details(scopus_data, api_key)

    # Normalize authors field to string for rendering
    for paper in paper_details:
        if isinstance(paper.get("Authors"), list):
            paper["Authors"] = ", ".join(paper["Authors"])

    # Proper categorization
    paper_details = [categorize_authors_in_paper(p, lecturers) for p in paper_details]

    staff_count = sum('Staff' in p.get('authors_category', []) for p in paper_details)
    student_count = sum('Student' in p.get('authors_category', []) for p in paper_details)

    if not paper_details:
        # ❌ No data from Scopus
        flash("Data not found. Please check your input.", "error")
        return redirect(url_for('index'))

    # Save to DB
    cursor = db.cursor()
    for paper in paper_details:
        scopus_id = safe(paper['SCOPUS_ID'])
        if scopus_id == "N/A":
            logging.warning(f"⚠️ Skipping paper with missing SCOPUS_ID.")
            continue

        cursor.execute("SELECT SCOPUS_ID FROM papers WHERE SCOPUS_ID = %s", (scopus_id,))
        if cursor.fetchone():
            logging.warning(f"⚠️ Skipping duplicate SCOPUS_ID: {scopus_id}")
            continue

        cursor.execute("""INSERT INTO papers (
            `SCOPUS_ID`, `EID`, `Title`, `Authors`, `Author IDs`, `Author Affiliations`,
            `Creators`, `Publication Name`, `eISSN`, `Volume`, `Page Range`, `Cover Date`,
            `DOI`, `Cited By Count`, `Aggregation Type`, `Subtype`, `Subtype Description`,
            `Source ID`, `Open Access`, `URL`, `Abstract`, `Subject Classification`,
            `Affiliation Names`, `Affiliation Cities`, `Affiliation Countries`
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", (
            scopus_id, safe(paper['EID']), safe(paper['Title']), paper['Authors'],
            ", ".join(paper['Author IDs']), ", ".join(paper['Author Affiliations']),
            safe(paper['Creators']), safe(paper['Publication Name']), safe(paper['eISSN']), safe(paper['Volume']),
            safe(paper['Page Range']), safe(paper['Cover Date']), safe(paper['DOI']), safe(paper['Cited By Count']),
            safe(paper['Aggregation Type']), safe(paper['Subtype']), safe(paper['Subtype Description']),
            safe(paper['Source ID']), safe(paper['Open Access']), safe(paper['URL']), safe(paper['Abstract']),
            safe(paper['Subject Classification']), safe(paper['Affiliation Names']),
            safe(paper['Affiliation Cities']), safe(paper['Affiliation Countries'])
        ))
        logging.info(f"✅ Inserted SCOPUS_ID: {scopus_id} | Title: {paper['Title'][:80]}")
    db.commit()
    cursor.close()

    session['scopus_ids'] = [paper['SCOPUS_ID'] for paper in paper_details]
    session['query'] = query
    session.modified = True

    flash("Data fetched successfully for selected", "success")
    # Redirect to result page after data insertion
    return redirect(url_for('result', page=1, sort_by='newest'))


@app.route('/result')
def result():

    # Get current page and sorting options
    page = request.args.get('page', 1, type=int)
    sort_by = request.args.get('sort_by', 'newest')  # Default sorting is by newest
    per_page = 10  # Number of results per page

    # Retrieve the query from session
    query = session.get('query', '')  # Retrieve query from session

    # Open the cursor to query the database
    cursor = db.cursor(dictionary=True)

    # Calculate the OFFSET based on page and results per page
    offset = (page - 1) * per_page

    # Modify the query based on the sorting option
    if sort_by == 'newest':
        order_by = "`Cover Date` DESC"
    elif sort_by == 'oldest':
        order_by = "`Cover Date` ASC"
    elif sort_by == 'alphabetical':
        order_by = "`Title` ASC"
    else:
        order_by = "`Cover Date` DESC"

    # Get papers for the current page with the applied sorting
    scopus_ids = session.get('scopus_ids', [])
    if not scopus_ids:
        flash("No search results found. Please perform a search first.", "warning")
        return redirect(url_for('index'))

    # Build the query
    format_strings = ','.join(['%s'] * len(scopus_ids))
    query_sql = f"SELECT * FROM papers WHERE SCOPUS_ID IN ({format_strings}) ORDER BY {order_by} LIMIT %s OFFSET %s"
    cursor.execute(query_sql, tuple(scopus_ids) + (per_page, offset))

    results = cursor.fetchall()
    cursor.close()

    # Get the total number of records for pagination
    cursor = db.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM papers WHERE SCOPUS_ID IN ({format_strings})", tuple(scopus_ids))
    total_records = cursor.fetchone()[0]
    cursor.close()

    # Fetch all matching papers (not paginated) to count total staff/student
    cursor = db.cursor(dictionary=True)
    cursor.execute(f"SELECT * FROM papers WHERE SCOPUS_ID IN ({format_strings})", tuple(scopus_ids))
    all_papers = cursor.fetchall()
    cursor.close()

    # Categorize all papers to count staff and students
    all_papers = [categorize_authors_in_paper(p, lecturers) for p in all_papers]

    staff_count = 0
    student_count = 0

    # Iterate over all papers to count staff and students correctly
    for paper in all_papers:
        # If there is any staff in the authors, count the paper as staff
        if "Staff" in paper["authors_category"]:
            staff_count += 1
        # If all authors are students, count as student
        elif all(cat == "Student" for cat in paper["authors_category"]):
            student_count += 1

    # Categorize the papers for the current page as well
    results = [categorize_authors_in_paper(paper, lecturers) for paper in results]

    # Calculate total pages
    total_pages = (total_records // per_page) + (1 if total_records % per_page > 0 else 0)
    total_results = total_records
    pagination_pages = get_pagination_pages(page, total_pages)

    # Render the results page with the sorted results
    return render_template(
        'result.html',
        results=results,
        query=query,
        page=page,
        per_page=per_page,  # Add this line
        total_pages=total_pages,
        total_results=total_results,
        sort_by=sort_by,
        pagination_pages=pagination_pages,
        staff_count=staff_count,
        student_count=student_count 
    )

def insert_paper_to_db(paper, cursor, error_log=None):
    scopus_id = safe(paper['SCOPUS_ID'])
    title = safe(paper['Title']).strip().lower()
    if scopus_id == "N/A" or title == "n/a":
        logging.warning(f"⚠️ Skipping paper with missing SCOPUS_ID or Title.")
        return

    logging.info(f"📝 Attempting to insert paper with Title: {paper['Title'][:80]}")

    try:
        # Check for duplicate based on Title (case-insensitive)
        cursor.execute("SELECT `Title` FROM papers WHERE LOWER(TRIM(`Title`)) = %s", (title,))
        existing_paper = cursor.fetchone()

        if existing_paper:
            logging.warning(f"⚠️ Duplicate detected by Title, skipping: {paper['Title'][:80]}")
            return

        # Insert record - wrap all column names with backticks (`)
        cursor.execute("""INSERT INTO papers (
            `SCOPUS_ID`, `EID`, `Title`, `Authors`, `Author IDs`, `Author Affiliations`,
            `Creators`, `Publication Name`, `eISSN`, `Volume`, `Page Range`, `Cover Date`,
            `DOI`, `Cited By Count`, `Aggregation Type`, `Subtype`, `Subtype Description`,
            `Source ID`, `Open Access`, `URL`, `Abstract`, `Subject Classification`,
            `Affiliation Names`, `Affiliation Cities`, `Affiliation Countries`
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", (
            scopus_id,
            safe(paper['EID']),
            paper['Title'],
            list_to_str(paper['Authors']),
            list_to_str(paper['Author IDs']),
            list_to_str(paper['Author Affiliations']),
            safe(paper['Creators']),
            safe(paper['Publication Name']),
            safe(paper['eISSN']),
            safe(paper['Volume']),
            safe(paper['Page Range']),
            safe(paper['Cover Date']),
            safe(paper['DOI']),
            safe(paper['Cited By Count']),
            safe(paper['Aggregation Type']),
            safe(paper['Subtype']),
            safe(paper['Subtype Description']),
            safe(paper['Source ID']),
            safe(paper['Open Access']),
            safe(paper['URL']),
            safe(paper['Abstract']),
            safe(paper['Subject Classification']),
            safe(paper['Affiliation Names']),
            safe(paper['Affiliation Cities']),
            safe(paper['Affiliation Countries'])
        ))

        db.commit()
        logging.info(f"✅ Successfully inserted paper with Title: {paper['Title'][:80]}")

    except Exception as e:
        msg = f"❌ Error inserting paper with Title: {paper['Title'][:80]} | Error: {str(e)}"
        logging.error(msg)
        if error_log is not None:
            error_log.append(msg)
        db.rollback()

# === 🔍 DETAIL VIEW ===
@app.route('/view/<string:doc_id>')
def view_detail(doc_id):
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM papers WHERE SCOPUS_ID = %s", (doc_id,))
    doc = cursor.fetchone()
    cursor.close()

    if doc:
        return render_template('view.html', doc=doc)
    else:
        flash("Document not found.", "danger")
        return redirect(url_for('result'))
    
@app.route('/view2/<string:doc_id>')
def view_detail2(doc_id):
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM papers WHERE SCOPUS_ID = %s", (doc_id,))
    doc = cursor.fetchone()
    cursor.close()

    if doc:
        return render_template('view2.html', doc=doc)
    else:
        flash("Document not found.", "danger")
        return redirect(url_for('documents'))
    
def search_author(first_name, last_name):
    query = f'AUTHLAST({last_name})'
    if first_name:
        query += f' AND AUTHFIRST({first_name})'

    params = {"query": query, "count": 1}
    url = "https://api.elsevier.com/content/search/author"
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}

    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 200:
        results = response.json().get("search-results", {}).get("entry", [])
        if results:
            author = results[0]
            full_name = f"{author.get('preferred-name', {}).get('given-name', '')} {author.get('preferred-name', {}).get('surname', '')}".strip() or "N/A"
            return {
                "author_id": author.get("dc:identifier", "").split(":")[-1],
                "name": full_name,
                "affiliation": author.get("affiliation-current", {}).get("affiliation-name", "N/A")
            }
    return None


def get_author_metrics(author_id):
    url = f"https://api.elsevier.com/content/author/author_id/{author_id}?view=ENHANCED"
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json().get("author-retrieval-response", [{}])[0]
        coredata = data.get("coredata", {})
        subjects = data.get("subject-areas", {}).get("subject-area", [])
        subject_areas = [s.get("$", "N/A") for s in subjects] if isinstance(subjects, list) else []

        return {
            "h_index": data.get("h-index", "N/A"),
            "citation_count": coredata.get("citation-count", "N/A"),
            "document_count": coredata.get("document-count", "N/A"),
            "subject_areas": ", ".join(subject_areas)
        }
    return {}


def get_author_documents(author_id):
    url = "https://api.elsevier.com/content/search/scopus"
    query = f"AU-ID({author_id})"
    params = {"query": query, "count": 200}
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}

    response = requests.get(url, headers=headers, params=params)

    documents = []
    if response.status_code == 200:
        for entry in response.json().get("search-results", {}).get("entry", []):
            documents.append({
                "scopus_id": entry.get("dc:identifier", "N/A").split(":")[-1],
                "title": entry.get("dc:title", "N/A"),
                "publication": entry.get("prism:publicationName", "N/A"),
                "date": entry.get("prism:coverDate", "N/A"),
                "doi": entry.get("prism:doi", "N/A"),
                "cited_by": entry.get("citedby-count", "0"),
                "url": entry.get("prism:url", "N/A")
            })
            time.sleep(1)
    return documents

def save_author_to_db(author_info, documents):
    cursor = db.cursor()

    # Insert into authors table (if not exists)
    cursor.execute("SELECT * FROM author WHERE author_id = %s", (author_info['author_id'],))
    if not cursor.fetchone():
        cursor.execute("""
            INSERT INTO author (author_id, name, affiliation, h_index, citation_count, document_count, subject_areas)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            author_info["author_id"], author_info["name"], author_info["affiliation"],
            author_info["h_index"], author_info["citation_count"],
            author_info["document_count"], author_info["subject_areas"]
        ))

    # Insert documents (skip duplicates)
    for doc in documents:
        cursor.execute("SELECT * FROM document WHERE scopus_id = %s", (doc["scopus_id"],))
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO document (author_id, scopus_id, title, publication, date, doi, cited_by, url)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                author_info["author_id"], doc["scopus_id"], doc["title"], doc["publication"],
                doc["date"], doc["doi"], doc["cited_by"], doc["url"]
            ))

    db.commit()
    cursor.close()
def get_pagination_pages(current_page, total_pages, max_pages=7):
    pages = []

    if total_pages <= max_pages:
        # Show all pages if total is less than max allowed
        return list(range(1, total_pages + 1))

    # Number of pages to show in the sliding window (excluding first and last pages)
    num_middle_pages = max_pages - 2  # reserve slots for first and last page

    # Calculate start and end for sliding window around current_page
    half_window = num_middle_pages // 2
    start_page = current_page - half_window
    end_page = current_page + half_window

    # Adjust if start_page is too close to beginning
    if start_page < 2:
        start_page = 2
        end_page = start_page + num_middle_pages - 1

    # Adjust if end_page is too close to the end
    if end_page > total_pages - 1:
        end_page = total_pages - 1
        start_page = end_page - num_middle_pages + 1

    # Add first page always
    pages.append(1)

    # Add ellipsis if there is a gap between first page and start_page
    if start_page > 2:
        pages.append('...')

    # Add the sliding window pages
    pages.extend(range(start_page, end_page + 1))

    # Add ellipsis if there is a gap between end_page and last page
    if end_page < total_pages - 1:
        pages.append('...')

    # Add last page always
    pages.append(total_pages)

    return pages


# Function for fuzzy search and logging
def fuzzy_search_author(first_name, last_name):
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT author_id, name FROM author")
    authors = cursor.fetchall()
    
    # Combine first and last name for the search query
    full_name = f"{first_name} {last_name}".strip().lower()
    
    # Use fuzzywuzzy for matching
    matches = process.extract(full_name, [author['name'].lower() for author in authors], limit=5)
    
    # Log the fuzzy search process
    logging.info(f"🔍 Performing fuzzy search for: {full_name}")
    
    similar_authors = []
    for match in matches:
        if match[1] > 80:  # Adjust threshold as needed
            # Find the exact match
            author = next(a for a in authors if a['name'].lower() == match[0])
            similar_authors.append(author)
            logging.info(f"✅ Found author: {author['name']} with match score: {match[1]}")

    cursor.close()
    return similar_authors

@app.route('/author_search', methods=['POST'])
def author_search():
    first_name = request.form.get('first_name', '').strip()
    last_name = request.form.get('last_name', '').strip()  # Capture last name too

    if not first_name:
        flash("First name is required.", "danger")
        logging.warning("❌ First name was not provided.")
        return redirect(url_for('index'))

    # Store the first name and last name in the session
    session['first_name'] = first_name
    session['last_name'] = last_name
    session.modified = True

    # Check if the author exists in the database using first name only
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM author WHERE name LIKE %s", (f"%{first_name}%",))  # Fetch only if author exists
    author = cursor.fetchone()
    cursor.close()

    if author:
        # If the author exists in the database, fetch the associated documents
        logging.info(f"✅ Found author: {author['name']} with author_id: {author['author_id']}")

        # Fetch documents related to the author from the database
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM document WHERE author_id = %s", (author["author_id"],))
        documents = cursor.fetchall()
        cursor.close()

        # Now display the data without saving anything
        flash(f"Author {author['name']} found with {len(documents)} documents.", "success")
        logging.info(f"✅ Displayed {len(documents)} documents for author {author['name']}.")

        return render_template('author_detail.html', author=author, documents=documents)  # Render the author and document details
    else:
        # If the author is not found in the database, fetch from Scopus
        flash(f"Author {first_name} {last_name} not found in the database. Fetching data from Scopus...", "warning")
        author_info = search_author(first_name, last_name)  # Pass both first_name and last_name

        if author_info:
            # If found through Scopus, save data to DB (optional)
            metrics = get_author_metrics(author_info["author_id"])
            author_info.update(metrics)

            documents = get_author_documents(author_info["author_id"])
            save_author_to_db(author_info, documents)

            flash(f"Author {author_info['name']} and {len(documents)} documents saved.", "success")
            logging.info(f"✅ Author {author_info['name']} and {len(documents)} documents saved.")
        else:
            flash(f"Author {first_name} {last_name} not found in Scopus.", "warning")

        return redirect(url_for('authors'))  # Redirect to the author list page

@app.route('/export_csv', methods=['POST'])
def export_csv():
    doc_ids = request.form.getlist('doc_ids')

    # Fetch the selected documents from the database
    cursor = db.cursor(dictionary=True)
    format_strings = ','.join(['%s'] * len(doc_ids))
    cursor.execute(f"SELECT * FROM papers WHERE SCOPUS_ID IN ({format_strings})", tuple(doc_ids))
    selected_docs = cursor.fetchall()
    cursor.close()

    # Create the CSV output using csv.DictWriter for safety
    from io import StringIO
    output = StringIO()
    fieldnames = [
        'SCOPUS_ID', 'EID', 'Title', 'Authors', 'Author IDs', 'Author Affiliations', 
        'Creators', 'Publication Name', 'eISSN', 'Volume', 'Page Range', 'Cover Date',
        'DOI', 'Cited By Count', 'Aggregation Type', 'Subtype', 'Subtype Description', 
        'Source ID', 'Open Access', 'URL', 'Abstract', 'Subject Classification', 
        'Affiliation Names', 'Affiliation Cities', 'Affiliation Countries'
    ]

    writer = csv.DictWriter(output, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
    writer.writeheader()

    for doc in selected_docs:
        # Convert any None values to 'N/A'
        for key in fieldnames:
            if doc.get(key) is None:
                doc[key] = 'N/A'
            elif isinstance(doc[key], list):
                doc[key] = '; '.join(str(item) for item in doc[key])

        writer.writerow({k: doc.get(k, 'N/A') for k in fieldnames})

    # Create response
    response = Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=exported_documents.csv"}
    )
    return response

# === 📄 Author List Page ===
@app.route('/authors')
def authors():
    cursor = db.cursor(dictionary=True)
    
    first_name = session.get('first_name', None)
    last_name = session.get('last_name', None)

    # Log session values for debugging
    logging.info(f"First Name from Session: {first_name}")
    logging.info(f"Last Name from Session: {last_name}")

    if first_name and last_name:
        cursor.execute("SELECT * FROM author WHERE name LIKE %s", (f"%{first_name}%",))
    else:
        cursor.execute("SELECT * FROM author ORDER BY name")

    authors = cursor.fetchall()
    logging.info(f"Fetched authors: {authors}")  # Log the authors list

    cursor.close()

    # Debugging: Check if the authors list is empty or has issues
    if not authors:
        logging.warning("No authors found in the database.")

    return render_template('result2.html', authors=authors)


# === 🔍 View Documents for a Specific Author ===
@app.route('/author/<author_id>')
def author_detail(author_id):
    cursor = db.cursor(dictionary=True)

    # Get author info
    cursor.execute("SELECT * FROM author WHERE author_id = %s", (author_id,))
    author = cursor.fetchone()

    # Get their documents
    cursor.execute("SELECT * FROM document WHERE author_id = %s", (author_id,))
    documents = cursor.fetchall()
    cursor.close()

    if not author:
        flash("Author not found.", "danger")
        return redirect(url_for('authors'))

    return render_template('author_detail.html', author=author, documents=documents)

@app.route('/export_all_results', methods=['POST'])
def export_all_results():
    # Get all scopus_ids from the session (these are the papers currently being displayed)
    scopus_ids = session.get('scopus_ids', [])
    if not scopus_ids:
        flash("No results to export.", "warning")
        return redirect(url_for('index'))  # If no results are found, redirect back to the index page

    # Fetch the papers from the database
    cursor = db.cursor(dictionary=True)
    format_strings = ','.join(['%s'] * len(scopus_ids))
    cursor.execute(f"SELECT * FROM papers WHERE SCOPUS_ID IN ({format_strings})", tuple(scopus_ids))
    selected_docs = cursor.fetchall()
    cursor.close()

    # Create CSV file from the results
    from io import StringIO
    output = StringIO()
    fieldnames = [
        'SCOPUS_ID', 'EID', 'Title', 'Authors', 'Author IDs', 'Author Affiliations',
        'Creators', 'Publication Name', 'eISSN', 'Volume', 'Page Range', 'Cover Date',
        'DOI', 'Cited By Count', 'Aggregation Type', 'Subtype', 'Subtype Description',
        'Source ID', 'Open Access', 'URL', 'Abstract', 'Subject Classification',
        'Affiliation Names', 'Affiliation Cities', 'Affiliation Countries'
    ]

    writer = csv.DictWriter(output, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
    writer.writeheader()

    for doc in selected_docs:
        # Convert any None values to 'N/A'
        for key in fieldnames:
            if doc.get(key) is None:
                doc[key] = 'N/A'
            elif isinstance(doc[key], list):
                doc[key] = '; '.join(str(item) for item in doc[key])

        writer.writerow({k: doc.get(k, 'N/A') for k in fieldnames})

    # Flash success message after the export
    flash("Data successfully exported!", "success")

    # Create the CSV file as a response
    response = Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=exported_all_documents.csv"}
    )
    return response

# app.py

@app.route('/admin_login', methods=['POST'])
def admin_login():
    password = request.form['password']
    
    if password == ADMIN_PASSWORD:
        # Store admin login status in session
        session['admin_logged_in'] = True
        flash("Successfully logged in!", "success")
        return redirect(url_for('lecturer_info'))  # Redirect to the lecturer info page
    else:
        flash("Incorrect password. Please try again.", "error")
        return redirect(url_for('index'))  # Redirect back to the index page

@app.route('/lecturer_info', methods=['GET', 'POST'])
def lecturer_info():
    # Check if the admin is logged in
    if not session.get('admin_logged_in'):
        flash("You need to log in first.", "warning")
        return redirect(url_for('index'))  # Redirect to login page if not logged in
    
    # Handle form submission to search lecturers
    search_query = request.args.get('search_query', '')

    cursor = db.cursor(dictionary=True)
    if search_query:
        cursor.execute("""
            SELECT * FROM lecturer
            WHERE LOWER(Nama Staf) LIKE %s OR LOWER(indexed_name) LIKE %s
        """, (f"%{search_query.lower()}%", f"%{search_query.lower()}%"))
    else:
        cursor.execute("SELECT * FROM lecturer")

    lecturers = cursor.fetchall()
    cursor.close()

    return render_template('lecturer.html', lecturers=lecturers, search_query=search_query)

@app.route('/logout')
def logout():
    session.pop('admin_logged_in', None)  # Remove the admin login status from the session
    flash("You have logged out.", "info")
    return redirect(url_for('index'))  # Redirect back to the index page

@app.route('/add_lecturer', methods=['POST'])
def add_lecturer():
    try:
        # Retrieve the form data
        staff_id = request.form['staff_id']
        name = request.form['name']
        department = request.form['department']
        first_name = request.form['first_name']
        last_name = request.form['last_name']
        indexed_name = request.form['indexed_name']

        # MySQL query to insert data into the lecturer table
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO lecturer (`No Staf`, `Nama Staf`, `Pusat Pengajian`, `First Name`, `Last Name`, `indexed_name`)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (staff_id, name, department, first_name, last_name, indexed_name))
        
        # Commit the transaction to the database
        db.commit()
        cursor.close()

        # Flash a success message and redirect to the lecturer info page
        flash("Lecturer added successfully!", "success")
    except Exception as e:
        # If an error occurs, flash an error message
        db.rollback()  # In case the transaction is not successful, roll it back
        cursor.close()
        flash(f"Error: {str(e)}. Lecturer was not added.", "error")

    return redirect(url_for('lecturer_info'))

@app.route('/update_lecturer', methods=['POST'])
def update_lecturer():
    staff_id = request.form['staff_id']
    name = request.form['name']
    department = request.form['department']
    first_name = request.form['first_name']
    last_name = request.form['last_name']
    indexed_name = request.form['indexed_name']

    try:
        cursor = db.cursor()
        cursor.execute("""
            UPDATE lecturer 
            SET `Nama Staf` = %s, `Pusat Pengajian` = %s, `First Name` = %s, `Last Name` = %s, `indexed_name` = %s
            WHERE `No Staf` = %s
        """, (name, department, first_name, last_name, indexed_name, staff_id))
        db.commit()
        cursor.close()

        flash("Lecturer updated successfully!", "success")
    except Exception as e:
        db.rollback()  # In case of error, rollback the transaction
        cursor.close()
        flash(f"Error: {str(e)}. Lecturer was not updated.", "error")

    return redirect(url_for('lecturer_info'))

@app.route('/delete_lecturer/<int:lecturer_id>', methods=['GET'])
def delete_lecturer(lecturer_id):
    try:
        cursor = db.cursor()
        cursor.execute("DELETE FROM lecturer WHERE `No Staf` = %s", (lecturer_id,))
        db.commit()
        cursor.close()

        flash("Lecturer deleted successfully!", "success")
    except Exception as e:
        db.rollback()  # In case of error, rollback the transaction
        cursor.close()
        flash(f"Error: {str(e)}. Lecturer was not deleted.", "error")

    return redirect(url_for('lecturer_info'))

import datetime

@app.route('/admin_sync_dashboard')
def admin_sync_dashboard():
    if not session.get('admin_logged_in'):
        flash("Admin login required.", "warning")
        return redirect(url_for('index'))

    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT DISTINCT YEAR(STR_TO_DATE(`Cover Date`, '%Y-%m-%d')) AS year FROM papers ORDER BY year DESC")
    paper_years = [row['year'] for row in cursor.fetchall() if row['year'] is not None]

    aff_ids = ["60002763", "60212344", "60228314", "60228313", "60212346"]
    aff_query = " OR ".join([f"AF-ID({aff_id})" for aff_id in aff_ids])

    sync_data = []

    # 1. Existing years from DB
    for year in paper_years:
        cursor.execute("SELECT COUNT(*) AS count FROM papers WHERE `Cover Date` LIKE %s", (f"{year}%",))
        db_count = cursor.fetchone()['count']

        full_query = f"PUBYEAR IS {year} AND ({aff_query})"

        headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
        params = {"query": full_query, "count": 0}
        response = requests.get(SCOPUS_SEARCH_URL, headers=headers, params=params)
        api_count = 0
        if response.status_code == 200:
            api_count = int(response.json().get("search-results", {}).get("opensearch:totalResults", 0))

        cursor.execute("SELECT * FROM scopus_sync_log WHERE year = %s", (year,))
        log = cursor.fetchone()

        sync_data.append({
            "year": year,
            "db_count": db_count,
            "api_count": api_count,
            "last_synced_at": log['last_synced_at'] if log else None
        })

    # 2. Check future years (from max year +1 to current_year + 5)
    current_year = datetime.datetime.now().year
    max_year = max(paper_years) if paper_years else current_year
    future_years = range(max_year + 1, current_year + 6)

    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}

    for year in future_years:
        full_query = f"PUBYEAR IS {year} AND ({aff_query})"
        params = {"query": full_query, "count": 0}
        response = requests.get(SCOPUS_SEARCH_URL, headers=headers, params=params)

        if response.status_code == 200:
            api_count = int(response.json().get("search-results", {}).get("opensearch:totalResults", 0))
            if api_count > 0:
                # No DB data yet, so db_count = 0, last_synced_at = None
                sync_data.append({
                    "year": year,
                    "db_count": 0,
                    "api_count": api_count,
                    "last_synced_at": None
                })

    cursor.close()

    # Sort all sync_data by year descending
    sync_data.sort(key=lambda x: x['year'], reverse=True)
    
    return render_template('admin_sync_dashboard.html', sync_data=sync_data)


# Function to save data to a CSV file with dynamic naming based on year
def save_to_csv(papers, year, file_path_template="scopus_data_{year}.csv"):
    file_path = file_path_template.format(year=year)
    fieldnames = [
        'SCOPUS_ID', 'EID', 'Title', 'Authors', 'Author IDs', 'Author Affiliations',
        'Creators', 'Publication Name', 'eISSN', 'Volume', 'Page Range', 'Cover Date',
        'DOI', 'Cited By Count', 'Aggregation Type', 'Subtype', 'Subtype Description',
        'Source ID', 'Open Access', 'URL', 'Abstract', 'Subject Classification',
        'Affiliation Names', 'Affiliation Cities', 'Affiliation Countries'
    ]

    # Overwrite CSV file instead of appending
    with open(file_path, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()  # Always write headers on overwrite

        for paper in papers:
            writer.writerow({
                'SCOPUS_ID': safe(paper['SCOPUS_ID']),
                'EID': safe(paper['EID']),
                'Title': paper['Title'],
                'Authors': list_to_str(paper['Authors']),
                'Author IDs': list_to_str(paper['Author IDs']),
                'Author Affiliations': list_to_str(paper['Author Affiliations']),
                'Creators': safe(paper['Creators']),
                'Publication Name': safe(paper['Publication Name']),
                'eISSN': safe(paper['eISSN']),
                'Volume': safe(paper['Volume']),
                'Page Range': safe(paper['Page Range']),
                'Cover Date': safe(paper['Cover Date']),
                'DOI': safe(paper['DOI']),
                'Cited By Count': safe(paper['Cited By Count']),
                'Aggregation Type': safe(paper['Aggregation Type']),
                'Subtype': safe(paper['Subtype']),
                'Subtype Description': safe(paper['Subtype Description']),
                'Source ID': safe(paper['Source ID']),
                'Open Access': safe(paper['Open Access']),
                'URL': safe(paper['URL']),
                'Abstract': safe(paper['Abstract']),
                'Subject Classification': safe(paper['Subject Classification']),
                'Affiliation Names': safe(paper['Affiliation Names']),
                'Affiliation Cities': safe(paper['Affiliation Cities']),
                'Affiliation Countries': safe(paper['Affiliation Countries'])
            })

    print(f"Data saved to CSV file: {file_path}")

def insert_csv_to_db(file_path="scopus_data.csv"):
    cursor = db.cursor()

    # Open and read the specific CSV file for the year
    with open(file_path, mode='r', encoding='utf-8') as file:
        reader = csv.DictReader(file)

        # Prepare the list of tuples for bulk insert
        rows_to_insert = []

        # Prepare the insert query
        query = """
            INSERT INTO papers (
                `SCOPUS_ID`, `EID`, `Title`, `Authors`, `Author IDs`, `Author Affiliations`,
                `Creators`, `Publication Name`, `eISSN`, `Volume`, `Page Range`, `Cover Date`,
                `DOI`, `Cited By Count`, `Aggregation Type`, `Subtype`, `Subtype Description`,
                `Source ID`, `Open Access`, `URL`, `Abstract`, `Subject Classification`,
                `Affiliation Names`, `Affiliation Cities`, `Affiliation Countries`
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
                `EID` = VALUES(`EID`),
                `Title` = VALUES(`Title`),
                `Authors` = VALUES(`Authors`),
                `Author IDs` = VALUES(`Author IDs`),
                `Author Affiliations` = VALUES(`Author Affiliations`),
                `Creators` = VALUES(`Creators`),
                `Publication Name` = VALUES(`Publication Name`),
                `eISSN` = VALUES(`eISSN`),
                `Volume` = VALUES(`Volume`),
                `Page Range` = VALUES(`Page Range`),
                `Cover Date` = VALUES(`Cover Date`),
                `DOI` = VALUES(`DOI`),
                `Cited By Count` = VALUES(`Cited By Count`),
                `Aggregation Type` = VALUES(`Aggregation Type`),
                `Subtype` = VALUES(`Subtype`),
                `Subtype Description` = VALUES(`Subtype Description`),
                `Source ID` = VALUES(`Source ID`),
                `Open Access` = VALUES(`Open Access`),
                `URL` = VALUES(`URL`),
                `Abstract` = VALUES(`Abstract`),
                `Subject Classification` = VALUES(`Subject Classification`),
                `Affiliation Names` = VALUES(`Affiliation Names`),
                `Affiliation Cities` = VALUES(`Affiliation Cities`),
                `Affiliation Countries` = VALUES(`Affiliation Countries`)
        """

        # Read the rows from the CSV file and prepare them for bulk insert
        for row in reader:
            rows_to_insert.append((
                row['SCOPUS_ID'],
                row['EID'],
                row['Title'],
                row['Authors'],
                row['Author IDs'],
                row['Author Affiliations'],
                row['Creators'],
                row['Publication Name'],
                row['eISSN'],
                row['Volume'],
                row['Page Range'],
                row['Cover Date'],
                row['DOI'],
                row['Cited By Count'],
                row['Aggregation Type'],
                row['Subtype'],
                row['Subtype Description'],
                row['Source ID'],
                row['Open Access'],
                row['URL'],
                row['Abstract'],
                row['Subject Classification'],
                row['Affiliation Names'],
                row['Affiliation Cities'],
                row['Affiliation Countries']
            ))

        # Perform bulk insert using executemany
        try:
            cursor.executemany(query, rows_to_insert)
            db.commit()
        except Exception as e:
            print(f"Error inserting data from CSV: {e}")
            db.rollback()

    cursor.close()
    print(f"Data successfully inserted from CSV into the database.")

# Updated sync_scopus_year function with CSV intermediate step
@app.route('/sync_year/<int:year>', methods=['POST'])
def sync_scopus_year(year):
    if not session.get('admin_logged_in'):
        flash("Admin login required.", "warning")
        return redirect(url_for('index'))

    error_log = []
    cursor = db.cursor()

    aff_ids = ["60002763", "60212344", "60228314", "60228313", "60212346"]
    aff_query = " OR ".join([f"AF-ID({aff_id})" for aff_id in aff_ids])
    full_query = f"PUBYEAR IS {year} AND ({aff_query})"
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}

    # Get total results count from API
    params = {"query": full_query, "count": 0}
    response = requests.get(SCOPUS_SEARCH_URL, headers=headers, params=params)
    if response.status_code != 200:
        flash(f"Failed to fetch API count for {year}.", "error")
        return redirect(url_for('admin_sync_dashboard'))

    api_count = int(response.json().get("search-results", {}).get("opensearch:totalResults", 0))

    # Count papers in DB for this year and affiliations
    cursor.execute("SELECT COUNT(*) FROM papers WHERE `Cover Date` LIKE %s", (f"{year}%",))
    db_count = cursor.fetchone()[0]

    if api_count <= db_count:
        flash(f"{year} is already up-to-date.", "info")
        cursor.close()
        return redirect(url_for('admin_sync_dashboard'))

    offset = 0
    batch_size = 50
    total_inserted = 0
    total_updated = 0
    all_papers = []

    while offset < api_count:
        params = {"query": full_query, "count": batch_size, "start": offset}
        response = requests.get(SCOPUS_SEARCH_URL, headers=headers, params=params)
        if response.status_code != 200:
            error_log.append(f"API error {response.status_code} at offset {offset}")
            break

        entries = response.json().get("search-results", {}).get("entry", [])
        if not entries:
            break

        papers = extract_paper_details({"search-results": {"entry": entries}}, api_key)
        all_papers.extend(papers)

        offset += batch_size
        time.sleep(1)

    # Save fetched papers to CSV
    save_to_csv(all_papers, year)

    # Now insert all data from the CSV into the DB
    insert_csv_to_db(f"scopus_data_{year}.csv")


    # Update sync log
    cursor.execute("""
        INSERT INTO scopus_sync_log (year, db_count, api_count, last_synced_at)
        VALUES (%s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE db_count=%s, api_count=%s, last_synced_at=NOW()
    """, (year, db_count + total_inserted, api_count, db_count + total_inserted, api_count))

    db.commit()
    cursor.close()

    if error_log:
        with open('sync_errors.log', 'a', encoding='utf-8') as f:
            for line in error_log:
                f.write(f"[{year}] {line}\n")

    flash(f"{total_inserted} new papers inserted, {total_updated} papers already exist (not updated) for year {year}.", "success")
    return redirect(url_for('admin_sync_dashboard'))


@app.route('/update_document', methods=['POST'])
def update_document():
    if not session.get('admin_logged_in'):
        flash("Admin login required.", "warning")
        return redirect(url_for('index'))
    try:
        scopus_id = request.form['scopus_id']
        title = request.form['title']
        authors = request.form['authors']
        cover_date = request.form['cover_date']
        # ... other fields ...

        cursor = db.cursor()
        cursor.execute("""
            UPDATE papers 
            SET Title = %s, Authors = %s, `Cover Date` = %s
            WHERE SCOPUS_ID = %s
        """, (title, authors, cover_date, scopus_id))
        db.commit()
        cursor.close()
        flash("Document updated successfully!", "success")
    except Exception as e:
        db.rollback()
        cursor.close()
        flash(f"Error updating document: {str(e)}", "error")
    return redirect(url_for('documents'))

@app.route('/delete_document/<string:scopus_id>', methods=['POST'])
def delete_document(scopus_id):
    if not session.get('admin_logged_in'):
        flash("Admin login required.", "warning")
        return redirect(url_for('index'))
    try:
        cursor = db.cursor()
        cursor.execute("DELETE FROM papers WHERE SCOPUS_ID = %s", (scopus_id,))
        db.commit()
        cursor.close()
        flash("Document deleted successfully!", "success")
    except Exception as e:
        db.rollback()
        cursor.close()
        flash(f"Error deleting document: {str(e)}", "error")
    return redirect(url_for('documents'))


@app.route('/documents')
def documents():
    if not session.get('admin_logged_in'):
        flash("Admin login required.", "warning")
        return redirect(url_for('index'))

    search_query = request.args.get('search_query', '')  # Get the search query from the form
    selected_year = request.args.get('year', '')  # Get the selected year from the form

    print(f"Search Query: {search_query}, Selected Year: {selected_year}")  # Add debug print

    cursor = db.cursor(dictionary=True)

    # Get all documents to extract unique years for the filter (no condition on the year)
    cursor.execute("SELECT `Cover Date` FROM papers")
    all_documents = cursor.fetchall()

    # Extract unique years from all documents (handle None values)
    years = sorted(set(doc['Cover Date'].year for doc in all_documents if doc['Cover Date'] is not None))

    # Initialize the query and parameters list
    query = "SELECT * FROM papers"
    params = []

    # Apply the search filter
    if search_query:
        query += " WHERE (LOWER(Title) LIKE %s OR LOWER(SCOPUS_ID) LIKE %s)"
        params.extend([f"%{search_query.lower()}%", f"%{search_query.lower()}%"])

    # Apply the year filter
    if selected_year:
        if 'WHERE' in query:
            query += " AND YEAR(`Cover Date`) = %s"
        else:
            query += " WHERE YEAR(`Cover Date`) = %s"
        params.append(selected_year)

    query += " ORDER BY `Cover Date` DESC"  # Ensure the results are ordered by cover date

    cursor.execute(query, tuple(params))
    documents = cursor.fetchall()

    # Count total results after applying filters (search query + year)
    total_results = len(documents)

    cursor.close()

    # Pass total results to the template
    return render_template('documents.html', documents=documents, search_query=search_query, years=years, selected_year=selected_year, total_results=total_results)



# === 🚀 LAUNCH ===
if __name__ == '__main__':
    app.run(debug=True)