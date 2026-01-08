# FLOSS Research Bibliography Collection 

A curated collection of bibliographic references on Free/Libre Open Source Software research, organised by topic in flat files. 
Curated by [Jose Teixeira](http://www.jteixeira.eu/) during his doctoral studies on ["Coopetition in an open-source way"](http://www.jteixeira.eu/dissertation/diss.pdf) in Information Systems at the University of Turku, Finland. 
This collection was originally intended for personal use but is now in the public domain to facilitate a more collaborative mapping of the current state of the art in open-source software research, thereby reducing the duplication of efforts among researchers on the topic.

```
          ________________________________________
         /                                       /|
        /_______________________________________/ |
       |                                       |  |
       |      FLOSS Research Literature        |  |
       |                                       |  |
       |   ┌─────────────────────────────┐     |  |
       |   │  Free Libre Open Source     │     |  |
       |   └─────────────────────────────┘     |  |
       |   ┌─────────────────────────────┐     |  |
       |   │ Coordination & collaboration│     |  |
       |   └─────────────────────────────┘     |  |
       |   ┌─────────────────────────────┐     |  |
       |   │    Diversity & gender       │     |  |
       |   └─────────────────────────────┘     |  |
       |   ┌─────────────────────────────┐     |  |
       |   │     Community fission       │     |  |
       |   └─────────────────────────────┘     |  |
       |   ┌─────────────────────────────┐     |  |
       |   │        Motivations          │     |  |
       |   └─────────────────────────────┘     |  |
       |   ┌─────────────────────────────┐     |  |
       |   │  Peer review / code reviews │     |  |
       |   └─────────────────────────────┘     |  |
       |   ┌─────────────────────────────┐     |  |
       |   │   Social Network Analysis   │     |  |
       |   └─────────────────────────────┘     | /
       |_______________________________________|/
```


## Format, files and topics.

The references are organised in bibliography database files (.bib files) by topic. 
The following bibliographic flat-file database files follow the BibTeX format. The standard is widely used and supported by reference management software (e.g., JabRef, Mendeley, Zotero, Google Scholar). 

- [**floss.bib**](floss.bib) - General FLOSS research. Special issues are annotated. Top journals in Software Engineering, Information Systems, Management and Innovation Studies are widely covered.  
  
- [**floss-coordination.bib**](floss-coordination.bib) - Studies of coordination and collaboration in FLOSS. Used on studies of coordination, collaboration and competition in the  [TensorFlow](https://users.aalto.fi/~apolinj2/tensorflowsna/), [Automotive Grade Linux](https://users.aalto.fi/~apolinj2/autogradelinuxsna/), OpenStack, Linux, Koha, WebKit software ecosystems by [Jose Teixeira](http://www.jteixeira.eu/). 
- [**floss-diversity.bib**](floss-diversity.bib) - Diversity, Inclusiveness and inclusion in FLOSS communities. 
- [**floss-fission.bib**](floss-fission.bib) - Forking, division, conflict and community schisms in FLOSS communities. 
- [**floss-gender.bib**](floss-gender.bib) - Gender studies in FLOSS. 
- [**floss-lis.bib**](floss-lis.bib) - Library and Information Science perspectives. Used by [Jose Teixeira](http://www.jteixeira.eu/) to study the [Koha](https://koha-community.org/) library system. 
- [**floss-motivations.bib**](floss-motivations.bib) - Developer and company motivations for open-sourcing. 
- [**floss-peer-review.bib**](floss-peer-review.bib) - Peer review  (aka code reviews) in FLOSS research. 
- [**floss-sna.bib**](floss-sna.bib) - Social Network Analysis of FLOSS communities.
- [**bibtoolrsc.rsc**](bibtoolrsc.rsc) -  Configuration file to style and generate citation keys.
- [**format_all_bib_with_bibtool.sh**](format_all_bib_with_bibtool.sh) - Small bash script that formats and generate BibTeX keys for all .bib files in the directory using bibtool and standard configuration defined in [**bibtoolrsc.rsc**](bibtoolrsc.rsc).

## Notations 

Several comments are used to annotate a BibTeX entry.  For example, if a entry have a  entry with comment = {AIS_basket_11, special_issue, literature_review} it denotes that the entry refers to a literature review article published in a premier Information Systems journal, on a edited special issue. 


###  Notation on the nature of the article 
- [special_issue] - Special issue paper
- [special_issue_editorial] - Special issue editorial
- [literature_review] - Review of literature  
- [systematic_literature_review] - Systematic review of literature
- [mixed_methods] - Mixed or multi methods
- [long] Longitudinal or panel data
- [meta] - Meta-study  paper 
- [design_science] - Design science paper 
- [network_analysis] - Network analysis paper
- [machine_learning] - Machine learning paper
- [new_method] - Claims a new method
- [new_theory] - Claims a new theory
- [floss_case_study] - Case study on FLOSS adoption or adoption
- [teaching_value] - With significant value to be used in teaching
- [tech_solution] - Describes the technical implementation of a solution for a known real world problem
- [book] - It's actually a book
- [edited_volume] - It's actually a edited research book
- [book_chapter] - It's actually a book chapter  Democratizing Innovation


### Notations on the prestige of the article 
- [BPA]  - Best paper award at the journal or conference in a single year
- [RU4BPA]  - Runner up, or second, for the best paper award at the journal or conference in a single year
- [PA] - Awared or recognized in some other way (e.g., best design science paper, best doctoral student paper, best empirical research paper)
- [high_cited]  High citations (1000+) according to Google Scholar
- [practice_value] - With significant evidence that it changed the work of real world practicioners 

### Notations on the prestige of the publication outlet 
- [FT50] - **50 academic and practitioner journals used by the Financial Times to rank faculty research at business schools**
  See [https://guides.lib.purdue.edu/ft50](https://guides.lib.purdue.edu/ft50)

  Here is the list formatted as an italicized Markdown list suitable for a GitHub `README.md` file:

*   *Academy of Management Journal*
*   *Academy of Management Review*
*   *Accounting, Organizations and Society*
*   *Accounting Review*
*   *Administrative Science Quarterly*
*   *American Economic Review*
*   *Contemporary Accounting Research*
*   *Econometrica*
*   *Entrepreneurship Theory and Practice*
*   *Harvard Business Review*
*   *Human Relations*
*   *Human Resource Management*
*   *Information Systems Research*
*   *Journal of Accounting & Economics*
*   *Journal of Accounting Research*
*   *Journal of Applied Psychology*
*   *Journal of Business Ethics*
*   *Journal of Business Venturing*
*   *Journal of Consumer Psychology*
*   *Journal of Consumer Research*
*   *Journal of Finance*
*   *Journal of Financial and Quantitative Analysis*
*   *Journal of Financial Economics*
*   *Journal of International Business Studies*
*   *Journal of Management*
*   *Journal of Management Information Systems*
*   *Journal of Management Studies*
*   *Journal of Marketing*
*   *Journal of Marketing Research*
*   *Journal of Operations Management*
*   *Journal of the Academy of Marketing Science*
*   *Management Science*
*   *Manufacturing and Service Operations Management*
*   *Marketing Science*
*   *MIS Quarterly*
*   *MIT Sloan Management Review*
*   *Operations Research*
*   *Organization Science*
*   *Organization Studies*
*   *Organizational Behavior and Human Decision Processes*
*   *Production and Operations Management*
*   *Quarterly Journal of Economics*
*   *Research Policy*
*   *Review of Accounting Studies*
*   *Review of Economic Studies*
*   *Review of Finance*
*   *Review of Financial Studies*
*   *Strategic Entrepreneurship Journal*
*   *Strategic Management Journal*

You can copy and paste this block directly into your `README.md` file.
-  [A*]  - **Excellent conference according to CORE (Computing Research and Education Association of Australasia) rankings**
  See [https://www.core.edu.au/icore-portal](https://www.core.edu.au/icore-portal)
- [AIS_basket_11] - **AIS basket of 11 premier IS journals**
  See [https://aisnet.org/page/SeniorScholarListofPremierJournals)(https://aisnet.org/page/SeniorScholarListofPremierJournals)


*   *Decision Support Systems*  
*   *European Journal of Information Systems*  
*   *Information & Management*  
*   *Information and Organization*  
*   *Information Systems Journal*  
*   *Information Systems Research*  
*   *Journal of the Association for Information Systems (JAIS)*  
*   *Journal of Information Technology*  
*   *Journal of Management Information Systems (JMIS)*  
*   *Journal of Strategic Information Systems*  
*   *MIS Quarterly*

- [TOP_SNA] **Top Network Analysis Journals:**


*   *American Sociological Review*  
*   *Network Science*  
*   *American Journal of Sociology*  
*   *Social Forces*  
*   *Sociological Science*  
*   *Proceedings of the National Academy of Sciences (PNAS)*  
*   *Connections*  
*   *Science*  
*   *Nature*

- [TOP_SE_J] **Top Software Engineering Journals:**

*   *IEEE Transactions on Software Engineering*  
*   *Journal of Systems and Software*  
*   *ACM Transactions on Software Engineering and Methodology*  
*   *Empirical Software Engineering*  
*   *Software: Practice and Experience*  
*   *IEEE Software*  
*   *Software and Systems Modeling*  
*   *Journal of Software: Evolution and Process*  
*   *Information and Software Technology*

- [TOP_SE_C] **Top Software Engineering Conferences:**

*   *ACM/IEEE International Conference on Software Engineering (ICSE)*  
*   *ACM SIGSOFT International Symposium on Foundations of Software Engineering (FSE)*  
*   *IEEE/ACM International Conference on Automated Software Engineering (ASE)*  
*   *ACM SIGPLAN Conference on Programming Language Design and Implementation (PLDI)*  
*   *International Symposium on Software Testing and Analysis (ISSTA)*  
*   *Mining Software Repositories (MSR)*  
*   *Symposium on Operating Systems Principles (SOSP)*  
*   *International Conference on Software Analysis, Evolution, and Reengineering (SANER)*  
*   *IEEE International Conference on Software Maintenance and Evolution (ICSME)*  
*   *Proceedings of the ACM on Programming Languages (PACMPL)*

- [TOP_IS_C] **Top Information Systems Conferences:**
*   *International Conference on Information Systems*
*   *European Conference on Information Systems* 

## Usage

### Directly 
Simply download individual .bib files and use them. 

### With Reference Managers
- **LaTeX**: They were tested by Jose Teixeira using both (1) BibTeX and BibLaTeX, (2) pdfLaTeX and XeLaTeX, (3) Linux and Mac. So it should work for you. 
- **Zotero**: Import .bib files directly
- **Mendeley**: Import .bib files directly
- **JabRef**: Native .bib support

## Statistics

- **Total curated publications:** 169

### Publication Types
- `article`: 119
- `inproceedings`: 40
- `book`: 5
- `incollection`: 3
- `inbook`: 2

### Top 10 Authors
| Rank | Author | Count |
|------|--------|-------|
| 1 | Fitzgerald, Brian | 11 |
| 2 | Crowston, Kevin  | 8 |
| 3 | A. Bosu | 7 |
| 4 | Howison, James | 5 |
| 6 | K. Z. Sultana | 4 |
| 7 | Robles, Gregorio | 4 |
| 8 | Feller, Joseph | 4 |
| 9 | Georg von Krogh | 4 |
| 10 | James Howison | 3 |

### Top 10 Journals
| Rank | Journal | Count |
|------|---------|-------|
| 1 | MIS Quarterly | 8 |
| 2 | Information Systems Research | 7 |
| 3 | Management Science | 7 |
| 4 | Research Policy | 6 |
| 5 | Information and Software Technology | 5 |
| 6 | Journal of Strategic Information Systems | 4 |
| 7 | First Monday | 4 |
| 8 | IEEE Transactions on Software Engineering | 3 |
| 9 | Empirical Software Engineering | 3 |
| 10 | Journal of the American Society for Information Science
and Technology | 3 |

### Top 10 Conferences
| Rank | Conference | Count |
|------|------------|-------|
| 1 | 2019 IEEE 26th International Conference on Software Analysis, Evolution and Reengineering (SANER) | 3 |
| 2 | the 38th Annual Hawaii International Conference on System Sciences | 3 |
| 3 | the 8th ACM/IEEE International Symposium on Empirical Software Engineering and Measurement | 2 |
| 4 | the European Conference on Information Systems (ECIS 2006) | 2 |
| 5 | Proceeding of the 40th International Conference on Information Systems (ICIS 2019) | 2 |
| 6 | Open Source Systems: Long-Term Sustainability | 2 |
| 7 | the International Conference on Information Systems (ICIS 2010) | 2 |
| 8 | 2019 ACM/IEEE International Symposium on Empirical Software Engineering and Measurement (ESEM) | 1 |
| 9 | the International Conference on Information Systems (ICIS 2017) | 1 |
| 10 | The International Symposium on Open Collaboration | 1 |

### Publication Years (Top 10)
| Rank | Year | Count |
|------|------|-------|
| 1 | 2006 | 20 |
| 2 | 2014 | 14 |
| 3 | 2005 | 12 |
| 4 | 2017 | 11 |
| 5 | 2007 | 10 |
| 6 | 2012 | 9 |
| 7 | 2019 | 8 |
| 8 | 2013 | 8 |
| 9 | 2009 | 7 |
| 10 | 2010 | 7 |

## Relational Social Network Analysis based on all the bibliographic collection .bib files
### Network Visualization

<!-- With custom size -->
[<img src="coauthorship_coauthorship_network.png" alt="Network" width="50%;">](coauthorship_coauthorship_network.png)

See [coauthorship_coauthorship_report.md](https://github.com/jaateixeira/FLOSS-research-bibliography-collection/blob/main/coauthorship_coauthorship_report.md) for a bibliometric report from a network perspective (i.e. more interested in relations than quantities). 

## Motivation 

 Motivations for releasing to the public domain this curated bibliometric collection include: 

| Motivation                                      | Description                                                                                     |
|--------------------------------------------------|-------------------------------------------------------------------------------------------------|
| **Poor quality of avaiable data**         |  Most of the references exported from [Google Scholar](https://scholar.google.com), [CrossRef](https://www.crossref.org), [Web of Science](https://www.webofscience.com), [Scopus](https://www.scopus.com), [EBSCOhost](https://www.ebsco.com), [IEEE Xplore](https://ieeexplore.ieee.org), [ACM Digital Library](https://dl.acm.org) often contain typos and are wrongly capitalized (e.g., missing the title case).|
| **Long-term Archival and Preservation**         | Ensures bibliometric data is preserved, accessible, and usable for future research.           |
| **Stimulate Collaboration Among FLOSS Researchers** | Encourages open collaboration, data sharing, and community-driven curation of bibliometric data.            |
| **Reduction of Duplicate Efforts**               | Minimizes redundant bibliometric data collection and processing across researchers and research groups.                    |
| **Support for Meta-Research**                    | Facilitates large-scale studies on research trends, impact, and open science practices.     |
| **Encouragement of Open Science Practices**     | Promotes FAIR (Findable, Accessible, Interoperable, Reusable) principles in research.          |
| **Community Engagement and Feedback**           | Allows for community contributions, corrections, and updates to the dataset. Improved bibliomentric data by peer reivew before merging               |
| **Interdisciplinary Research Support**          | Enables researchers from different fields to access and analyze bibliometric data.           |
| **Historical and Trend Analysis**               | Supports longitudinal studies of research evolution, citation patterns, and collaboration.   |



## Contributing to the project: 
Fork the project, clone, branch and push. See [Contributing to a project by GitHub](https://docs.github.com/en/get-started/exploring-projects-on-github/contributing-to-a-project). 

Then, if you want to keep this alive and help tp maintain this map of the current state of the art of research in open-source software, send a pull request on GitHub, and it will be merged after testing.  

Knowledge of [BibTool](http://www.gerd-neugebauer.de/software/TeX/BibTool/en/) and [BibTeX Tidy](https://flamingtempura.github.io/bibtex-tidy/index.html) will not hurt. 

Avoid submitting duplicates or incomplete bibliometric entries. 

Process and clean a BibTeX file using `bibtool` with custom resource rules, if you have the skills. 

```sh
bibtool -v -d -k -r bibtoolrsc.rsc -F [in file] -o [out file]
```

You can use the  [**format_all_bib_with_bibtool.sh**](format_all_bib_with_bibtool.sh) small bash script that formats and generate BibTeX keys for all .bib files in the directory using bibtool and the standard [**bibtoolrsc.rsc**](bibtoolrsc.rsc) configurations.

## License
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/legalcode.txt)
Pick items as you wish. Cite or attribute if using any of the files as a whole. 
