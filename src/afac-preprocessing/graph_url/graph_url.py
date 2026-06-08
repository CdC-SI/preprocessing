# Support pour réaliser le graphe
# https://medium.com/@jainsnehasj6/networkx-for-python-a-practical-guide-to-graphs-visualization-and-traversals-35106cfee2ea
# video Youtube : https://www.youtube.com/watch?v=o5USzpzKm6o

import jsonlines
import networkx as nx
from pathlib import Path
import matplotlib.pyplot as plt
from pyvis.network import Network

# Root
project_root = Path(__file__).resolve().parent.parent
stage3_dir = project_root / "data" / "output_files" / "stage3_test" # Changer chemin si nécessaire

graph = nx.Graph() # Créé le graphe vide

url_nodes = {}
url_counter = 1

print("URLs détectées dans tous les sous-dossiers :")
for doc_dir in sorted(stage3_dir.iterdir()): # iterdir permet de parcourir les sous-dossiers
    if not doc_dir.is_dir(): # Si ce n'est pas un dossier, on va au suivant
        continue

    doc_name = doc_dir.name + ".pdf"
    if not graph.has_node(doc_name):
        graph.add_node(doc_name, type="Document", title=doc_name) # créé le noeud pour le document

    for jsonl_file in doc_dir.glob("hyperlinks_data_*.jsonl"): # On récupère les URLs dans les fichiers jsonl
        with jsonlines.open(jsonl_file) as reader:
            for obj in reader:
                url  = obj.get("hyperlink", "").strip()
                text = obj.get("text", "").strip()
                if not url:
                    continue

                print(f"[{doc_name}] {text or '(no text)'} -> {url}") # Affiche les URLs détectées avec leur texte associé (ou "(no text)" si pas de texte)

                # Déduplication par (url, text) : même URL avec texte différent = noeud différent
                key = (url, text)
                if key not in url_nodes:
                    node_name = text if text else f"URL {url_counter}"
                    # Évite les collisions si le même texte pointe vers des URLs différentes
                    if graph.has_node(node_name):
                        node_name = f"{text} ({url_counter})" if text else f"URL {url_counter}"
                    url_nodes[key] = node_name
                    graph.add_node(
                        node_name,
                        type="URL",
                        url = url,
                        label = node_name,
                        title = f"{text}\n{url}", # tooltip dans PyVis
                    )
                    url_counter += 1

                # Arête : document "cites" URL pour le moment un seul tag possbile "cites" 
                graph.add_edge(doc_name, url_nodes[key], relation="cites")


print(f"\nTotal URLs uniques : {len(url_nodes)}")
print(f"Total documents : {sum(1 for n, d in graph.nodes(data=True) if d['type'] == 'Document')}")

# Visualisation statique (PNG)
position = nx.spring_layout(graph, k=1.5, seed=42)
plt.figure(figsize=(14, 10))

node_types = [graph.nodes[node].get("type", "Other") for node in graph.nodes]
color_map = {
    "URL": "lightblue",
    "Document": "lightgreen",
    "Other": "lightgrey",
}
colors = [color_map.get(nt, "lightgrey") for nt in node_types]

nx.draw(graph, position, with_labels=True, node_color=colors, node_size=2000, font_size=8, font_weight="bold")
edge_labels = nx.get_edge_attributes(graph, "relation")
nx.draw_networkx_edge_labels(graph, position, edge_labels=edge_labels, font_color="red", font_size=7)

graph_output_dir = project_root / "graph_url" / "graph_output"
graph_output_dir.mkdir(parents=True, exist_ok=True)

graph_path = graph_output_dir / "graph_url.png"
plt.savefig(graph_path, bbox_inches="tight")
print(f"\nStatic graph saved  : {graph_path}")

# Visualisation interactive (HTML)
net = Network(notebook=False, height="900px", width="100%", bgcolor="#FFFFFF", font_color="black")

# Set edge labels for PyVis
for u, v, data in graph.edges(data=True):
    if "relation" in data:
        graph[u][v]["label"] = data["relation"]

# Set node colors for PyVis
for n, d in graph.nodes(data=True):
    if d.get("type") == "URL":
        graph.nodes[n]["color"] = "lightblue"
    elif d.get("type") == "Document":
        graph.nodes[n]["color"] = "lightgreen"
    else:
        graph.nodes[n]["color"] = "lightgrey"

# Set edge colors for PyVis
for u, v, data in graph.edges(data=True):
    if data.get("relation") == "cites":
        graph[u][v]["color"] = "red"
net.from_nx(graph)

# Ajoute le tooltip (title) pour chaque noeud
for node in graph.nodes(data=True):
    n_id = node[0]
    n_data = node[1]
    net.get_node(n_id)["title"] = n_data.get("title", n_id)

interactive_path = graph_output_dir / "graph_interactive.html"
net.show(str(interactive_path), notebook=False)
print(f"Interactive graph saved: {interactive_path}")