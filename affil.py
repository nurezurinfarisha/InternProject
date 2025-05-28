import requests

def get_affiliation_id(affiliation_name, api_key):
    url = "https://api.elsevier.com/content/search/affiliation"
    headers = {
        "X-ELS-APIKey": api_key,
        "Accept": "application/json"
    }
    params = {
        "query": f"affil({affiliation_name})"
    }

    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print(f"Error: {response.status_code} - {response.text}")
        return None

    entries = response.json().get("search-results", {}).get("entry", [])
    if not entries:
        print("No affiliations found.")
        return None

    # Display and return the first match
    affil_id = entries[0].get("dc:identifier", "").replace("AFFILIATION_ID:", "")
    affil_name = entries[0].get("affiliation-name")
    print(f"Found affiliation: {affil_name} (ID: {affil_id})")
    return affil_id

def get_documents_by_affiliation(affiliation_id, api_key):
    url = "https://api.elsevier.com/content/search/scopus"
    headers = {
        "X-ELS-APIKey": api_key,
        "Accept": "application/json"
    }
    params = {
        "query": f"af-id({affiliation_id})",
        "count": 25  # number of documents to return
    }

    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print(f"Error fetching documents: {response.status_code} - {response.text}")
        return

    entries = response.json().get("search-results", {}).get("entry", [])
    if not entries:
        print("No documents found.")
        return

    print(f"\n--- Documents from affiliation ID {affiliation_id} ---")
    for idx, doc in enumerate(entries, start=1):
        title = doc.get("dc:title", "N/A")
        authors = doc.get("dc:creator", "N/A")
        year = doc.get("prism:coverDate", "N/A")[:4]
        subtype_desc = doc.get("subtypeDescription", "N/A")

        print(f"\nDocument #{idx}")
        print("Title:", title)
        print("Authors:", authors)
        print("Year:", year)
        print("Publication Type:", subtype_desc)

def main():
    affiliation_name = input("Enter affiliation name: ")
    api_key = input("Enter your Elsevier API Key: ")

    affil_id = get_affiliation_id(affiliation_name, api_key)
    if affil_id:
        get_documents_by_affiliation(affil_id, api_key)

if __name__ == "__main__":
    main()
