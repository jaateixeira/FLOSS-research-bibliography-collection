#!/usr/bin/env python3
"""
BibTeX Co-authorship Network Analyzer
Creates co-authorship networks from BibTeX files and outputs visualization.
"""

import re
import sys
import argparse
import os
import itertools
from collections import defaultdict, Counter
from typing import Dict, List, Set, Tuple
import networkx as nx
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from matplotlib.colors import Normalize
import numpy as np

class CoAuthorshipNetwork:
    def __init__(self):
        self.authors_publications = defaultdict(set)  # author -> set of publication keys
        self.publications_authors = defaultdict(set)  # publication key -> set of authors
        self.author_names = {}  # Normalized author names
        self.publication_info = {}  # publication key -> info
        
    def parse_bibtex_file(self, filepath: str) -> List[Dict]:
        """Parse a BibTeX file and extract entries."""
        entries = []
        current_entry = None
        entry_content = []
        in_entry = False
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            with open(filepath, 'r', encoding='latin-1') as f:
                lines = f.readlines()
        
        for line in lines:
            line = line.strip()
            
            # Skip comments and empty lines
            if not line or line.startswith('%'):
                continue
            
            # Look for entry start
            if not in_entry and '@' in line:
                match = re.search(r'@(\w+)\s*{', line)
                if match:
                    in_entry = True
                    entry_type = match.group(1).lower()
                    # Extract key (first part after {)
                    key_match = re.search(r'{([^,]+)', line)
                    entry_key = key_match.group(1) if key_match else 'unknown'
                    current_entry = {
                        'type': entry_type,
                        'key': entry_key,
                        'fields': {},
                        'raw': []
                    }
                    entry_content = [line]
                    
                    # Check if entry ends on same line
                    if '}' in line and line.count('{') <= line.count('}'):
                        in_entry = False
                        current_entry['raw'] = entry_content
                        entries.append(current_entry)
                continue
            
            if in_entry:
                entry_content.append(line)
                # Check if entry ends
                if '}' in line:
                    brace_count = line.count('{') - line.count('}')
                    # Simple heuristic: if we've closed all braces
                    if brace_count <= 0 and line.rstrip().endswith('}'):
                        in_entry = False
                        # Parse the complete entry
                        full_text = '\n'.join(entry_content)
                        parsed_fields = self.parse_entry_fields(full_text)
                        current_entry['fields'] = parsed_fields
                        entries.append(current_entry)
        
        return entries
    
    def parse_entry_fields(self, entry_text: str) -> Dict:
        """Parse fields from a BibTeX entry."""
        fields = {}
        
        # Remove the @type{key, part
        lines = entry_text.split('\n')
        if len(lines) > 1:
            content = '\n'.join(lines[1:])
        else:
            content = lines[0]
            # Remove the first part
            content = content[content.find(',') + 1:]
        
        # Find all field=value pairs
        pattern = r'(\w+)\s*=\s*({.*?}|".*?"|.*?)(?=\s*\w+\s*=|\s*}\s*$)'
        matches = re.findall(pattern, content, re.DOTALL | re.IGNORECASE)
        
        for field_name, field_value in matches:
            field_name = field_name.lower().strip()
            field_value = field_value.strip()
            
            # Clean up the value
            if field_value.startswith('{') and field_value.endswith('}'):
                field_value = field_value[1:-1]
            elif field_value.startswith('"') and field_value.endswith('"'):
                field_value = field_value[1:-1]
            
            # Remove trailing commas
            field_value = field_value.rstrip(',')
            fields[field_name] = field_value.strip()
        
        return fields
    
    def normalize_author_name(self, author: str) -> str:
        """Normalize author name for consistent matching."""
        # Convert to lowercase
        normalized = author.lower()
        
        # Remove extra spaces
        normalized = re.sub(r'\s+', ' ', normalized)
        
        # Remove titles and suffixes
        normalized = re.sub(r'\b(dr\.?|prof\.?|mr\.?|mrs\.?|ms\.?|ph\.?d\.?)\b', '', normalized)
        
        # Remove non-alphanumeric characters (keep spaces)
        normalized = re.sub(r'[^\w\s]', '', normalized)
        
        # Standardize name format (last, first -> first last)
        if ',' in normalized:
            parts = normalized.split(',')
            if len(parts) >= 2:
                normalized = f"{parts[1].strip()} {parts[0].strip()}"
        
        # Remove extra spaces again
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        
        return normalized.title()  # Capitalize first letters
    
    def extract_authors(self, author_field: str, publication_key: str) -> List[str]:
        """Extract and normalize authors from author field."""
        if not author_field:
            return []
        
        # Split by "and" (case insensitive)
        authors = re.split(r'\sand\s', author_field, flags=re.IGNORECASE)
        
        # Clean and normalize author names
        cleaned_authors = []
        for author in authors:
            author = author.strip()
            # Remove any remaining braces or quotes
            author = author.replace('{', '').replace('}', '').replace('"', '')
            if author:
                normalized = self.normalize_author_name(author)
                if normalized:
                    cleaned_authors.append(normalized)
                    
                    # Store the display name for this normalized form
                    if normalized not in self.author_names:
                        self.author_names[normalized] = author
        
        return cleaned_authors
    
    def process_publication(self, entry: Dict) -> None:
        """Process a single publication and update co-authorship data."""
        publication_key = entry['key']
        author_field = entry['fields'].get('author', '')
        
        if not author_field:
            return
        
        authors = self.extract_authors(author_field, publication_key)
        
        if not authors:
            return
        
        # Store publication info
        self.publication_info[publication_key] = {
            'title': entry['fields'].get('title', 'Unknown Title'),
            'year': entry['fields'].get('year', 'Unknown'),
            'type': entry['type'],
            'authors': authors
        }
        
        # Update author-publication mappings
        self.publications_authors[publication_key] = set(authors)
        for author in authors:
            self.authors_publications[author].add(publication_key)
    
    def build_network(self, files: List[str]) -> nx.Graph:
        """Build co-authorship network from BibTeX files."""
        print("Building co-authorship network...", file=sys.stderr)
        
        # Parse all files and process publications
        total_publications = 0
        for filepath in files:
            print(f"  Processing {os.path.basename(filepath)}...", file=sys.stderr)
            entries = self.parse_bibtex_file(filepath)
            for entry in entries:
                self.process_publication(entry)
                total_publications += 1
        
        print(f"  Processed {total_publications} publications", file=sys.stderr)
        print(f"  Found {len(self.authors_publications)} unique authors", file=sys.stderr)
        
        # Create network
        G = nx.Graph()
        
        # Add authors as nodes
        for author, pubs in self.authors_publications.items():
            G.add_node(author, 
                      publications=len(pubs),
                      display_name=self.author_names.get(author, author))
        
        # Add edges for co-authorship
        edge_weights = defaultdict(int)
        
        for publication, authors in self.publications_authors.items():
            # For each pair of authors in this publication
            for author1, author2 in itertools.combinations(authors, 2):
                # Sort to ensure consistent edge direction
                pair = tuple(sorted([author1, author2]))
                edge_weights[pair] += 1
        
        # Add weighted edges to graph
        for (author1, author2), weight in edge_weights.items():
            G.add_edge(author1, author2, weight=weight, publications=weight)
        
        print(f"  Created network with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges", file=sys.stderr)
        
        return G
    
    def calculate_network_metrics(self, G: nx.Graph) -> Dict:
        """Calculate network metrics."""
        metrics = {}
        
        # Basic metrics
        metrics['num_nodes'] = G.number_of_nodes()
        metrics['num_edges'] = G.number_of_edges()
        metrics['density'] = nx.density(G)
        
        # Degree statistics
        degrees = dict(G.degree())
        metrics['avg_degree'] = np.mean(list(degrees.values()))
        metrics['max_degree'] = max(degrees.values())
        
        # Weighted degree (strength)
        strengths = {}
        for node in G.nodes():
            strength = sum(G[node][neighbor]['weight'] for neighbor in G.neighbors(node))
            strengths[node] = strength
        
        metrics['avg_strength'] = np.mean(list(strengths.values()))
        metrics['max_strength'] = max(strengths.values())
        
        # Centrality measures
        metrics['degree_centrality'] = nx.degree_centrality(G)
        
        # Betweenness centrality (sample for large networks)
        if len(G) <= 500:
            metrics['betweenness_centrality'] = nx.betweenness_centrality(G)
        else:
            # Sample for large networks
            metrics['betweenness_centrality'] = nx.betweenness_centrality(G, k=min(100, len(G)))
        
        # Clustering coefficient
        metrics['clustering'] = nx.clustering(G)
        metrics['avg_clustering'] = nx.average_clustering(G)
        
        # Connected components
        components = list(nx.connected_components(G))
        metrics['num_components'] = len(components)
        metrics['largest_component_size'] = max(len(c) for c in components) if components else 0
        
        # Get top authors by various metrics
        metrics['top_degree'] = sorted(degrees.items(), key=lambda x: x[1], reverse=True)[:20]
        metrics['top_strength'] = sorted(strengths.items(), key=lambda x: x[1], reverse=True)[:20]
        metrics['top_betweenness'] = sorted(metrics['betweenness_centrality'].items(), 
                                          key=lambda x: x[1], reverse=True)[:20]
        
        # Most frequent collaborations
        edge_weights = {(u, v): d['weight'] for u, v, d in G.edges(data=True)}
        metrics['top_collaborations'] = sorted(edge_weights.items(), key=lambda x: x[1], reverse=True)[:20]
        
        return metrics
    
    def export_network(self, G: nx.Graph, output_prefix: str, metrics: Dict = None) -> None:
        """Export network in various formats."""
        # Export as GEXF (for Gephi)
        gexf_file = f"{output_prefix}_coauthorship.gexf"
        nx.write_gexf(G, gexf_file)
        print(f"  Network exported to {gexf_file}", file=sys.stderr)
        
        # Export as GraphML
        graphml_file = f"{output_prefix}_coauthorship.graphml"
        nx.write_graphml(G, graphml_file)
        print(f"  Network exported to {graphml_file}", file=sys.stderr)
        
        # Export edge list
        edge_list_file = f"{output_prefix}_coauthorship.edgelist"
        nx.write_edgelist(G, edge_list_file)
        print(f"  Network exported to {edge_list_file}", file=sys.stderr)
        
        # Export node list with attributes
        node_list_file = f"{output_prefix}_authors.csv"
        with open(node_list_file, 'w', encoding='utf-8') as f:
            f.write("author,display_name,publications,degree,strength,betweenness,clustering\n")
            for node in G.nodes():
                display = self.author_names.get(node, node)
                pubs = len(self.authors_publications.get(node, []))
                degree = G.degree(node)
                strength = sum(G[node][nbr]['weight'] for nbr in G.neighbors(node))
                betweenness = metrics['betweenness_centrality'].get(node, 0) if metrics else 0
                clustering = metrics['clustering'].get(node, 0) if metrics else 0
                f.write(f'"{node}","{display}",{pubs},{degree},{strength},{betweenness},{clustering}\n')
        print(f"  Author list exported to {node_list_file}", file=sys.stderr)
        
        # Export edge list with weights
        edge_csv_file = f"{output_prefix}_collaborations.csv"
        with open(edge_csv_file, 'w', encoding='utf-8') as f:
            f.write("author1,author2,weight,common_publications\n")
            for u, v, data in G.edges(data=True):
                weight = data.get('weight', 1)
                f.write(f'"{u}","{v}",{weight},{weight}\n')
        print(f"  Collaboration list exported to {edge_csv_file}", file=sys.stderr)
    
    def visualize_network(self, G: nx.Graph, output_prefix: str, metrics: Dict = None) -> None:
        """Create visualization of the co-authorship network."""
        print("Creating network visualizations...", file=sys.stderr)
        
        # Only visualize if network is not too large
        if G.number_of_nodes() > 200:
            print(f"  Network too large ({G.number_of_nodes()} nodes) for full visualization", file=sys.stderr)
            print(f"  Visualizing largest connected component only", file=sys.stderr)
            
            # Get largest connected component
            components = list(nx.connected_components(G))
            largest = max(components, key=len)
            H = G.subgraph(largest).copy()
            
            if len(H) > 200:
                print(f"  Largest component still too large ({len(H)} nodes)", file=sys.stderr)
                print(f"  Exporting data files only (use Gephi for visualization)", file=sys.stderr)
                return
        else:
            H = G
        
        # Set up figure
        plt.figure(figsize=(16, 12))
        
        # Use spring layout
        print(f"  Computing layout for {H.number_of_nodes()} nodes...", file=sys.stderr)
        pos = nx.spring_layout(H, k=1/np.sqrt(len(H)), iterations=50, seed=42)
        
        # Calculate node sizes based on degree
        degrees = dict(H.degree())
        node_sizes = [max(50, degrees[node] * 30) for node in H.nodes()]
        
        # Calculate edge widths based on weight
        edge_weights = [H[u][v]['weight'] for u, v in H.edges()]
        max_weight = max(edge_weights) if edge_weights else 1
        edge_widths = [1 + (w / max_weight) * 4 for w in edge_weights]
        
        # Node colors based on betweenness centrality
        if metrics:
            betweenness = metrics['betweenness_centrality']
            node_colors = [betweenness.get(node, 0) for node in H.nodes()]
            norm = Normalize(vmin=min(node_colors), vmax=max(node_colors))
            cmap = cm.viridis
        else:
            node_colors = 'skyblue'
        
        # Draw the network
        nx.draw_networkx_nodes(H, pos, node_size=node_sizes, 
                              node_color=node_colors if isinstance(node_colors, str) else node_colors,
                              cmap=None if isinstance(node_colors, str) else cmap,
                              alpha=0.8)
        
        nx.draw_networkx_edges(H, pos, width=edge_widths, alpha=0.3, edge_color='gray')
        
        # Only label important nodes (high degree)
        if H.number_of_nodes() <= 50:
            # Label all nodes
            labels = {node: self.author_names.get(node, node) for node in H.nodes()}
            nx.draw_networkx_labels(H, pos, labels=labels, font_size=8, font_weight='bold')
        else:
            # Label top nodes by degree
            top_nodes = sorted(degrees.items(), key=lambda x: x[1], reverse=True)[:20]
            top_labels = {node: self.author_names.get(node, node) for node, _ in top_nodes}
            nx.draw_networkx_labels(H, pos, labels=top_labels, font_size=8, font_weight='bold')
        
        plt.title(f"Co-authorship Network ({H.number_of_nodes()} authors, {H.number_of_edges()} collaborations)")
        plt.axis('off')
        
        # Save the figure
        output_file = f"{output_prefix}_coauthorship_network.png"
        plt.tight_layout()
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"  Visualization saved to {output_file}", file=sys.stderr)
        
        # Create a second visualization focusing on the core
        if H.number_of_nodes() > 30:
            self.create_core_visualization(H, output_prefix, metrics, pos)
    
    def create_core_visualization(self, G: nx.Graph, output_prefix: str, metrics: Dict, pos: Dict) -> None:
        """Create a visualization focusing on the core of the network."""
        # Extract k-core (removing peripheral nodes)
        k_core = nx.k_core(G)
        
        if len(k_core) > 10:  # Only if core is meaningful
            plt.figure(figsize=(12, 10))
            
            # Layout for core
            core_pos = nx.spring_layout(k_core, k=1/np.sqrt(len(k_core)), iterations=100, seed=42)
            
            # Node sizes based on degree in original graph
            degrees = dict(G.degree())
            core_sizes = [max(100, degrees[node] * 50) for node in k_core.nodes()]
            
            # Edge widths
            core_edge_weights = [k_core[u][v]['weight'] for u, v in k_core.edges()]
            max_core_weight = max(core_edge_weights) if core_edge_weights else 1
            core_edge_widths = [2 + (w / max_core_weight) * 6 for w in core_edge_weights]
            
            # Draw core network
            nx.draw_networkx_nodes(k_core, core_pos, node_size=core_sizes, 
                                  node_color='lightcoral', alpha=0.9)
            
            nx.draw_networkx_edges(k_core, core_pos, width=core_edge_widths, 
                                  alpha=0.5, edge_color='darkred')
            
            # Label all nodes in core
            core_labels = {node: self.author_names.get(node, node) for node in k_core.nodes()}
            nx.draw_networkx_labels(k_core, core_pos, labels=core_labels, 
                                   font_size=10, font_weight='bold')
            
            plt.title(f"Core Co-authorship Network (k-core, {len(k_core)} authors)")
            plt.axis('off')
            
            # Save core visualization
            core_file = f"{output_prefix}_coauthorship_core.png"
            plt.tight_layout()
            plt.savefig(core_file, dpi=300, bbox_inches='tight')
            plt.close()
            
            print(f"  Core visualization saved to {core_file}", file=sys.stderr)
    
    def generate_report(self, G: nx.Graph, metrics: Dict, output_prefix: str) -> None:
        """Generate a comprehensive report in Markdown format."""
        report_file = f"{output_prefix}_coauthorship_report.md"
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("# Co-authorship Network Analysis Report\n\n")
            
            f.write("## Network Overview\n\n")
            f.write(f"- **Number of authors (nodes):** {metrics['num_nodes']}\n")
            f.write(f"- **Number of collaborations (edges):** {metrics['num_edges']}\n")
            f.write(f"- **Network density:** {metrics['density']:.4f}\n")
            f.write(f"- **Average degree:** {metrics['avg_degree']:.2f}\n")
            f.write(f"- **Maximum degree:** {metrics['max_degree']}\n")
            f.write(f"- **Average weighted degree (strength):** {metrics['avg_strength']:.2f}\n")
            f.write(f"- **Maximum strength:** {metrics['max_strength']}\n")
            f.write(f"- **Average clustering coefficient:** {metrics['avg_clustering']:.4f}\n")
            f.write(f"- **Number of connected components:** {metrics['num_components']}\n")
            f.write(f"- **Size of largest component:** {metrics['largest_component_size']}\n\n")
            
            f.write("## Top Authors by Degree\n\n")
            f.write("| Rank | Author | Degree | Publications |\n")
            f.write("|------|--------|--------|--------------|\n")
            for i, (author, degree) in enumerate(metrics['top_degree'][:15], 1):
                pubs = len(self.authors_publications.get(author, []))
                display_name = self.author_names.get(author, author)
                f.write(f"| {i} | {display_name} | {degree} | {pubs} |\n")
            f.write("\n")
            
            f.write("## Top Authors by Collaboration Strength\n\n")
            f.write("| Rank | Author | Strength | Publications |\n")
            f.write("|------|--------|----------|--------------|\n")
            for i, (author, strength) in enumerate(metrics['top_strength'][:15], 1):
                pubs = len(self.authors_publications.get(author, []))
                display_name = self.author_names.get(author, author)
                f.write(f"| {i} | {display_name} | {strength:.0f} | {pubs} |\n")
            f.write("\n")
            
            f.write("## Most Central Authors (Betweenness Centrality)\n\n")
            f.write("| Rank | Author | Betweenness |\n")
            f.write("|------|--------|-------------|\n")
            for i, (author, betweenness) in enumerate(metrics['top_betweenness'][:15], 1):
                display_name = self.author_names.get(author, author)
                f.write(f"| {i} | {display_name} | {betweenness:.4f} |\n")
            f.write("\n")
            
            f.write("## Strongest Collaborations\n\n")
            f.write("| Rank | Author 1 | Author 2 | Joint Publications |\n")
            f.write("|------|----------|----------|--------------------|\n")
            for i, ((author1, author2), weight) in enumerate(metrics['top_collaborations'][:15], 1):
                display1 = self.author_names.get(author1, author1)
                display2 = self.author_names.get(author2, author2)
                f.write(f"| {i} | {display1} | {display2} | {weight} |\n")
            f.write("\n")
            
            f.write("## Network Components\n\n")
            components = list(nx.connected_components(G))
            f.write(f"- **Total components:** {len(components)}\n")
            f.write("- **Component sizes:** ")
            component_sizes = sorted([len(c) for c in components], reverse=True)
            f.write(", ".join(str(size) for size in component_sizes[:10]))
            if len(component_sizes) > 10:
                f.write(f", ... ({len(component_sizes)-10} more)")
            f.write("\n\n")
            
            f.write("## Exported Files\n\n")
            f.write("- `*_coauthorship.gexf`: Network in GEXF format (for Gephi)\n")
            f.write("- `*_coauthorship.graphml`: Network in GraphML format\n")
            f.write("- `*_coauthorship.edgelist`: Edge list format\n")
            f.write("- `*_coauthorship_network.png`: Network visualization\n")
            f.write("- `*_authors.csv`: Author attributes\n")
            f.write("- `*_collaborations.csv`: Collaboration list\n")
            if os.path.exists(f"{output_prefix}_coauthorship_core.png"):
                f.write("- `*_coauthorship_core.png`: Core network visualization\n")
            
        print(f"  Report generated: {report_file}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(
        description='Create co-authorship network from BibTeX files.'
    )
    parser.add_argument(
        'files',
        nargs='+',
        help='BibTeX files to analyze (.bib extension)'
    )
    parser.add_argument(
        '-o', '--output',
        default='coauthorship',
        help='Output file prefix (default: "coauthorship")'
    )
    parser.add_argument(
        '--no-viz',
        action='store_true',
        help='Skip visualization (for very large networks)'
    )
    parser.add_argument(
        '--min-publications',
        type=int,
        default=1,
        help='Minimum number of publications for authors to include (default: 1)'
    )
    
    args = parser.parse_args()
    
    # Check if files exist
    for filepath in args.files:
        if not os.path.exists(filepath):
            print(f"Error: File '{filepath}' not found.", file=sys.stderr)
            sys.exit(1)
    
    # Create analyzer and build network
    analyzer = CoAuthorshipNetwork()
    G = analyzer.build_network(args.files)
    
    # Filter authors with few publications if requested
    if args.min_publications > 1:
        print(f"Filtering authors with at least {args.min_publications} publications...", file=sys.stderr)
        nodes_to_remove = []
        for node in G.nodes():
            if len(analyzer.authors_publications.get(node, [])) < args.min_publications:
                nodes_to_remove.append(node)
        
        G.remove_nodes_from(nodes_to_remove)
        print(f"  Removed {len(nodes_to_remove)} authors, {G.number_of_nodes()} remaining", file=sys.stderr)
    
    if G.number_of_nodes() == 0:
        print("No authors meet the criteria. Network is empty.", file=sys.stderr)
        sys.exit(0)
    
    # Calculate network metrics
    print("Calculating network metrics...", file=sys.stderr)
    metrics = analyzer.calculate_network_metrics(G)
    
    # Export network data
    print("Exporting network data...", file=sys.stderr)
    analyzer.export_network(G, args.output, metrics)
    
    # Create visualization (unless disabled or network is too large)
    if not args.no_viz and G.number_of_nodes() <= 1000:
        analyzer.visualize_network(G, args.output, metrics)
    
    # Generate report
    print("Generating report...", file=sys.stderr)
    analyzer.generate_report(G, metrics, args.output)
    
    print(f"\nAnalysis complete! Files saved with prefix '{args.output}_'", file=sys.stderr)

if __name__ == '__main__':
    main()