import requests
import csv
import time

# 🔐 Scopus API Key
API_KEY = "af632813f032e8279a40f4cc7084f7b5"

# Scopus API headers
HEADERS = {
    "X-ELS-APIKey": API_KEY,
    "Accept": "application/json"
}

# Scopus endpoints
AUTHOR_SEARCH_URL = "https://api.elsevier.com/content/search/author"
AUTHOR_RETRIEVAL_URL = "https://api.elsevier.com/content/author/author_id/"
AUTHOR_DOCS_URL = "https://api.elsevier.com/content/search/scopus"

# 🔍 Search author by name
def search_author(first_name, last_name):
    query = f'AUTHLAST({last_name})'
    if first_name:
        query += f' AND AUTHFIRST({first_name})'

    params = {"query": query, "count": 1}
    response = requests.get(AUTHOR_SEARCH_URL, headers=HEADERS, params=params)

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
    else:
        print(f"❌ Error {response.status_code} during author search: {response.text}")
    return None

# 📊 Get detailed metrics from Author Retrieval API
def get_author_metrics(author_id):
    url = f"{AUTHOR_RETRIEVAL_URL}{author_id}?view=ENHANCED"
    response = requests.get(url, headers=HEADERS)

    if response.status_code == 200:
        data = response.json().get("author-retrieval-response", [{}])[0]
        coredata = data.get("coredata", {})

        subject_areas = []
        subjects = data.get("subject-areas", {}).get("subject-area", [])
        if isinstance(subjects, list):
            subject_areas = [s.get("$", "N/A") for s in subjects]

        return {
            "h_index": data.get("h-index", "N/A"),
            "citation_count": coredata.get("citation-count", "N/A"),
            "document_count": coredata.get("document-count", "N/A"),
            "subject_areas": subject_areas
        }
    else:
        print(f"❌ Error {response.status_code} in author metrics fetch: {response.text}")
    return {}


# 📚 Get documents by author ID
def get_author_documents(author_id):
    query = f"AU-ID({author_id})"
    params = {"query": query, "count": 200}
    response = requests.get(AUTHOR_DOCS_URL, headers=HEADERS, params=params)

    documents = []
    if response.status_code == 200:
        entries = response.json().get("search-results", {}).get("entry", [])
        for entry in entries:
            documents.append({
                "SCOPUS_ID": entry.get("dc:identifier", "N/A"),
                "Title": entry.get("dc:title", "N/A"),
                "Publication": entry.get("prism:publicationName", "N/A"),
                "Date": entry.get("prism:coverDate", "N/A"),
                "DOI": entry.get("prism:doi", "N/A"),
                "Cited By": entry.get("citedby-count", "0"),
                "URL": entry.get("prism:url", "N/A")
            })
            time.sleep(1)  # 🔁 Avoid API throttling
    else:
        print(f"❌ Error {response.status_code} fetching documents: {response.text}")
    return documents

# 💾 Save everything to CSV
def save_author_data_csv(author_info, documents, filename="scopus_author_data3.csv"):
    with open(filename, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        # Metadata
        writer.writerow(["Author Name", author_info["name"]])
        writer.writerow(["Author ID", author_info["author_id"]])
        writer.writerow(["Affiliation", author_info["affiliation"]])
        writer.writerow(["h-index", author_info["h_index"]])
        writer.writerow(["Total Documents", author_info["document_count"]])
        writer.writerow(["Total Citations", author_info["citation_count"]])
        writer.writerow(["Subject Areas", ", ".join(author_info["subject_areas"])])
        writer.writerow([])

        # Documents
        if documents:
            writer.writerow(["SCOPUS_ID", "Title", "Publication", "Date", "DOI", "Cited By", "URL"])
            for doc in documents:
                writer.writerow([
                    doc["SCOPUS_ID"],
                    doc["Title"],
                    doc["Publication"],
                    doc["Date"],
                    doc["DOI"],
                    doc["Cited By"],
                    doc["URL"]
                ])

    print(f"✅ Author data saved to {filename}")

# 🚀 Main flow
def main():
    print("\n=== Scopus Author Miner ===\n")
    last_name = input("Enter author's last name: ").strip()
    first_name = input("Enter author's first name (optional): ").strip()

    print("\n🔍 Searching for author...")
    author_info = search_author(first_name, last_name)

    if author_info:
        print(f"\n🎯 Found Author: {author_info['name']} (ID: {author_info['author_id']})")

        print("📈 Retrieving metrics...")
        metrics = get_author_metrics(author_info["author_id"])
        author_info.update(metrics)

        print("📚 Retrieving documents...")
        documents = get_author_documents(author_info["author_id"])

        save_author_data_csv(author_info, documents)
    else:
        print("❌ Author not found.")

if __name__ == "__main__":
    main()
