import requests
import csv
import time

# API Endpoints
SCOPUS_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
SCOPUS_ABSTRACT_URL = "https://api.elsevier.com/content/abstract/scopus_id/"
SCOPUS_AFFILIATION_URL = "https://api.elsevier.com/content/affiliation/"

# Function to get Scopus search results
# Function to get Scopus search results
def get_scopus_data(query, api_key):
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    params = {"query": query, "count": 200}  # Adjust count as needed

    response = requests.get(SCOPUS_SEARCH_URL, headers=headers, params=params)
    
    if response.status_code == 200:
        data = response.json()
        
        # Extract total results count
        total_results = data.get("search-results", {}).get("opensearch:totalResults", "0")
        
        # Debugging output
        print(f"\n🔎 Total Results Found: {total_results}\n")
        
        return data
    else:
        print(f"⚠️ Error {response.status_code}: {response.text}")
        return None



# Function to get abstract based on Scopus ID
def get_abstract(scopus_id, api_key):
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    response = requests.get(SCOPUS_ABSTRACT_URL + scopus_id, headers=headers)
    
    if response.status_code == 200:
        return response.json().get("abstracts-retrieval-response", {}).get("coredata", {}).get("dc:description", "N/A")
    return "N/A"

# Function to retrieve full affiliation details using Affiliation API
def get_affiliation(affil_id, api_key):
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    response = requests.get(SCOPUS_AFFILIATION_URL + affil_id, headers=headers)
    
    if response.status_code == 200:
        affil_data = response.json().get("affiliation-retrieval-response", {})
        return affil_data.get("affiliation-name", "N/A"), affil_data.get("country", "N/A")
    return "N/A", "N/A"

# Function to retrieve authors, their IDs, and affiliations
def get_authors(scopus_id, api_key):
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    response = requests.get(SCOPUS_ABSTRACT_URL + scopus_id, headers=headers)

    if response.status_code == 200:
        data = response.json().get("abstracts-retrieval-response", {}).get("authors", {}).get("author", [])

        author_names = []
        author_ids = []
        author_affiliations = []

        if isinstance(data, list):
            for author in data:
                author_name = author.get("ce:indexed-name", "N/A")
                author_id = author.get("@auid", "N/A")

                # Handle multiple affiliations
                affiliations = author.get("affiliation", [])  
                if isinstance(affiliations, list):
                    affil_ids = [affil.get("@id", "N/A") for affil in affiliations]
                else:
                    affil_ids = [affiliations.get("@id", "N/A")]  # If it's a dict, convert to list

                author_names.append(author_name)
                author_ids.append(author_id)
                author_affiliations.append("; ".join(affil_ids) if affil_ids else "N/A")

        return author_names, author_ids, author_affiliations

    return [], [], []
# Function to retrieve subject classification using Scopus Abstract API
def get_subject_classification(scopus_id, api_key):
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    url = f"https://api.elsevier.com/content/abstract/scopus_id/{scopus_id}"
    
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        
        # ✅ DEBUG: Print API response to check structure
        print(f"\n🔍 DEBUG: API response for {scopus_id}:\n", data)
        
        subject_areas = data.get("abstracts-retrieval-response", {}).get("subject-areas", {}).get("subject-area", [])

        subjects = []
        
        # ✅ Properly handle different cases: list, dict, or missing data
        if isinstance(subject_areas, list):
            subjects = [s.get("$", "N/A") for s in subject_areas if isinstance(s, dict)]
        elif isinstance(subject_areas, dict):
            subjects = [subject_areas.get("$", "N/A")]

        return ", ".join(subjects) if subjects else "N/A"
    
    print(f"⚠️ Error {response.status_code} for Scopus ID {scopus_id}: {response.text}")
    return "N/A"


# Function to extract paper details
def extract_paper_details(data, api_key):
    papers = []
    
    if data and "search-results" in data:
        for entry in data["search-results"].get("entry", []):
            scopus_id = entry.get("dc:identifier", "").split(":")[-1]
            
            # Fetch additional details
            abstract = get_abstract(scopus_id, api_key)
            subjects = get_subject_classification(scopus_id, api_key)  # ✅ Fix: Direct assignment
            authors, author_ids, author_affiliations = get_authors(scopus_id, api_key)

            page_range = entry.get("prism:pageRange", "N/A")

            # Extract affiliation details
            affiliations = entry.get("affiliation", [])
            affil_names, affil_cities, affil_countries = [], [], []

            if isinstance(affiliations, list):
                for affil in affiliations:
                    affil_name = affil.get("affilname", "N/A")
                    affil_city = affil.get("affiliation-city", None)  # ✅ Keep None initially
                    affil_country = affil.get("affiliation-country", "N/A")

                    affil_names.append(affil_name)
                    affil_cities.append(affil_city if affil_city else "N/A")  # ✅ Ensure no None values
                    affil_countries.append(affil_country)

            # Append all available data fields
            papers.append({
                "SCOPUS_ID": entry.get("dc:identifier", "N/A"),
                "EID": entry.get("eid", "N/A"),
                "Title": entry.get("dc:title", "N/A"),
                "Authors": authors,
                "Author IDs": author_ids,
                "Author Affiliations": author_affiliations,
                "Creators": entry.get("dc:creator", "N/A"),
                "Publication Name": entry.get("prism:publicationName", "N/A"),
                "eISSN": entry.get("prism:eIssn", "N/A"),
                "Volume": entry.get("prism:volume", "N/A"),
                "Page Range": page_range,
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
                "Affiliation Names": ", ".join(affil_names) if affil_names else "N/A",
                "Affiliation Cities": ", ".join(affil_cities) if isinstance(affil_cities, list) else "N/A",
                "Affiliation Countries": ", ".join(affil_countries) if affil_countries else "N/A"
            })

            # Sleep to avoid hitting API rate limits
            time.sleep(1)

    return papers

# Function to save data to CSV
def save_to_csv(papers, filename="scopus_data2.csv"):
    keys = papers[0].keys() if papers else []
    with open(filename, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=keys)
        writer.writeheader()
        writer.writerows(papers)
    print(f"✅ Data saved to {filename}")

# Main execution
if __name__ == "__main__":
    api_key = "af632813f032e8279a40f4cc7084f7b5"  
    title_filter = input("Enter title keyword (optional): ").strip()
    user_query = input("Enter your Scopus search keywords (comma-separated): ").strip()
    publication_year = input("Enter publication year or range (e.g., 2020 or 2015-2020, leave blank to ignore): ").strip()
    affiliation = input("Enter affiliation (e.g., Harvard University, leave blank to ignore): ").strip()

    # Convert comma-separated keywords into 'AND' format
    if "," in user_query:
        formatted_query = " AND ".join([word.strip() for word in user_query.split(",")])
    else:
        formatted_query = user_query.strip()  # Single keyword remains unchanged
    # Add title filter if provided
    if title_filter:
        formatted_query += f' AND TITLE("{title_filter}")'
    # Handle year filter (single year or range)
    if publication_year:
        if "-" in publication_year:  # User entered a range (e.g., 2015-2020)
            start_year, end_year = publication_year.split("-")
            if start_year.isdigit() and end_year.isdigit():  # Ensure valid numbers
                formatted_query += f" AND (PUBYEAR = {start_year} OR PUBYEAR = {end_year})"
        elif publication_year.isdigit():  # Single year (e.g., 2023)
            formatted_query += f" AND PUBYEAR = {publication_year}"

            # Handle affiliation filter
        if affiliation:
            formatted_query += f' AND AFFIL("{affiliation}")'  # Scopus uses AFFIL() for affiliation filtering

    print(f"🔍 Searching Scopus for: {formatted_query}")
    scopus_data = get_scopus_data(formatted_query, api_key)
    papers = extract_paper_details(scopus_data, api_key)

    if papers:
        save_to_csv(papers)
    else:
        print("❌ No data found.")
