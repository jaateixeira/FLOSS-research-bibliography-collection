#!/usr/bin/env python3
"""
BibTeX Statistics Analyzer
Analyzes BibTeX files and outputs publication statistics in Markdown format.
"""

import re
import sys
import argparse
from collections import Counter
from typing import Dict, List, Tuple, Set
import os

class BibtexAnalyzer:
    def __init__(self):
        self.entries = []
        
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
        # This regex handles multi-line values and nested braces
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
    
    def extract_authors(self, author_field: str) -> List[str]:
        """Extract individual authors from author field."""
        if not author_field:
            return []
        
        # Split by "and" (case insensitive)
        authors = re.split(r'\sand\s', author_field, flags=re.IGNORECASE)
        
        # Clean author names
        cleaned_authors = []
        for author in authors:
            author = author.strip()
            # Remove extra spaces and commas
            author = re.sub(r'\s+', ' ', author)
            # Remove any remaining braces or quotes
            author = author.replace('{', '').replace('}', '')
            if author:
                cleaned_authors.append(author)
        
        return cleaned_authors
    
    def extract_venue_info(self, entry: Dict) -> Tuple[str, str]:
        """Extract journal or conference information."""
        fields = entry['fields']
        
        # Check for journal
        journal = fields.get('journal', '') or fields.get('journaltitle', '')
        
        # Check for conference/booktitle
        conference = fields.get('booktitle', '') or fields.get('conference', '')
        
        # Clean venue names
        if journal:
            journal = journal.replace('{', '').replace('}', '')
            # Remove common prefixes/suffixes
            journal = re.sub(r'^proceedings of\s*', '', journal, flags=re.IGNORECASE)
            journal = re.sub(r'^the\s+', '', journal, flags=re.IGNORECASE)
        
        if conference:
            conference = conference.replace('{', '').replace('}', '')
            conference = re.sub(r'^proceedings of\s*', '', conference, flags=re.IGNORECASE)
        
        return journal.strip(), conference.strip()
    
    def analyze_files(self, filepaths: List[str]) -> Dict:
        """Analyze multiple BibTeX files and return statistics."""
        all_entries = []
        
        for filepath in filepaths:
            print(f"Processing {filepath}...", file=sys.stderr)
            entries = self.parse_bibtex_file(filepath)
            all_entries.extend(entries)
        
        return self.calculate_statistics(all_entries)
    
    def calculate_statistics(self, entries: List[Dict]) -> Dict:
        """Calculate statistics from BibTeX entries."""
        stats = {
            'total_entries': len(entries),
            'entry_types': Counter(),
            'authors': Counter(),
            'journals': Counter(),
            'conferences': Counter(),
            'years': Counter()
        }
        
        for entry in entries:
            # Count entry types
            stats['entry_types'][entry['type']] += 1
            
            # Extract and count authors
            author_field = entry['fields'].get('author', '')
            if author_field:
                authors = self.extract_authors(author_field)
                for author in authors:
                    stats['authors'][author] += 1
            
            # Extract venue information
            journal, conference = self.extract_venue_info(entry)
            if journal:
                stats['journals'][journal] += 1
            if conference:
                stats['conferences'][conference] += 1
            
            # Extract year
            year = entry['fields'].get('year', '')
            if year:
                # Extract just the year (might have extra characters)
                year_match = re.search(r'\d{4}', year)
                if year_match:
                    stats['years'][year_match.group()] += 1
        
        return stats
    
    def format_markdown(self, stats: Dict, filenames: List[str]) -> str:
        """Format statistics as Markdown."""
        md_output = []
        
        # Header
        md_output.append("# BibTeX Statistics Report")
        md_output.append("")
        
        # File information
        md_output.append("## Files Analyzed")
        for filename in filenames:
            md_output.append(f"- `{os.path.basename(filename)}`")
        md_output.append("")
        
        # Summary statistics
        md_output.append("## Summary Statistics")
        md_output.append(f"- **Total publications:** {stats['total_entries']}")
        md_output.append("")
        
        # Entry types
        if stats['entry_types']:
            md_output.append("### Publication Types")
            for entry_type, count in sorted(stats['entry_types'].items(), key=lambda x: x[1], reverse=True):
                md_output.append(f"- `{entry_type}`: {count}")
            md_output.append("")
        
        # Top authors
        if stats['authors']:
            md_output.append("### Top 10 Authors")
            md_output.append("| Rank | Author | Count |")
            md_output.append("|------|--------|-------|")
            for i, (author, count) in enumerate(stats['authors'].most_common(10), 1):
                # Escape pipe characters in author names
                author_escaped = author.replace('|', '\\|')
                md_output.append(f"| {i} | {author_escaped} | {count} |")
            md_output.append("")
        
        # Top journals
        if stats['journals']:
            md_output.append("### Top 10 Journals")
            md_output.append("| Rank | Journal | Count |")
            md_output.append("|------|---------|-------|")
            for i, (journal, count) in enumerate(stats['journals'].most_common(10), 1):
                # Escape pipe characters
                journal_escaped = journal.replace('|', '\\|')
                md_output.append(f"| {i} | {journal_escaped} | {count} |")
            md_output.append("")
        
        # Top conferences
        if stats['conferences']:
            md_output.append("### Top 10 Conferences")
            md_output.append("| Rank | Conference | Count |")
            md_output.append("|------|------------|-------|")
            for i, (conference, count) in enumerate(stats['conferences'].most_common(10), 1):
                # Escape pipe characters
                conference_escaped = conference.replace('|', '\\|')
                md_output.append(f"| {i} | {conference_escaped} | {count} |")
            md_output.append("")
        
        # Year distribution (top 10 years)
        if stats['years']:
            md_output.append("### Publication Years (Top 10)")
            md_output.append("| Rank | Year | Count |")
            md_output.append("|------|------|-------|")
            for i, (year, count) in enumerate(stats['years'].most_common(10), 1):
                md_output.append(f"| {i} | {year} | {count} |")
            md_output.append("")
        
        return '\n'.join(md_output)

def main():
    parser = argparse.ArgumentParser(
        description='Analyze BibTeX files and generate statistics in Markdown format.'
    )
    parser.add_argument(
        'files',
        nargs='+',
        help='BibTeX files to analyze (.bib extension)'
    )
    parser.add_argument(
        '-o', '--output',
        help='Output file (default: stdout)'
    )
    
    args = parser.parse_args()
    
    # Check if files exist
    for filepath in args.files:
        if not os.path.exists(filepath):
            print(f"Error: File '{filepath}' not found.", file=sys.stderr)
            sys.exit(1)
    
    # Analyze files
    analyzer = BibtexAnalyzer()
    stats = analyzer.analyze_files(args.files)
    
    # Generate Markdown output
    md_output = analyzer.format_markdown(stats, args.files)
    
    # Output results
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(md_output)
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(md_output)

if __name__ == '__main__':
    main()