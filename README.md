# FLOSS Research Bibliography Collection 

A curated collection of bibliographic references on Free/Libre Open Source Software research, organized by topic. 
Curated by [Jose Teixeira](http://www.jteixeira.eu/) during his doctoral studies on ["Coopetition in an open-source way"](http://www.jteixeira.eu/dissertation/diss.pdf) in Information Systems at the University of Turku, Finland. 
They were intended for personal use, but are now in the public domain to facilitate mapping the current state of the art in open-source software research in a collaborative way. 


          ________________________________________
         /                                       /|
        /_______________________________________/ |
       |                                       |  |
       |  FLOSS Research Literature            |  |
       |                                       |  |
       |  ┌─────────────────────────────┐      |  |
       |  │  Free Libre Open Source     │      |  |
       |  └─────────────────────────────┘      |  |
       |  ┌─────────────────────────────┐      |  |
       |  │ Coordination & collaboration│      |  |
       |  └─────────────────────────────┘      |  |
       |  ┌─────────────────────────────┐      |  |
       |  │    Diversity & Gender       │      |  |
       |  └─────────────────────────────┘      |  |
       |  ┌─────────────────────────────┐      |  |
       |  │     Commonity fission       │      |  |
       |  └─────────────────────────────┘      |  |
       |  ┌─────────────────────────────┐      |  |
       |  │        Motivations          │      |  |
       |  └─────────────────────────────┘      |  |
       |  ┌─────────────────────────────┐      |  |
       |  │        Peer review          │      |  |
       |  └─────────────────────────────┘      |  |
       |  ┌─────────────────────────────┐      |  |
       |  │ Social Network Analysis     │      |  |
       |  └─────────────────────────────┘      | /
       |_______________________________________|/


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


## Usage

### Directly 
Simply download individual .bib files and use them. 


### With Reference Managers
- **LaTeX**: They were tested by Jose Teixeira using both (1) BibTeX and BibLaTeX, (2) pdfLaTeX and XeLaTeX, (3) Linux and Mac. So it should work for you. 
- **Zotero**: Import .bib files directly
- **Mendeley**: Import .bib files directly
- **JabRef**: Native .bib support

## Statistics
[Add counts of entries per file, most cited papers, etc.]

## Contributing to the project: 
Fork the project, clone, branch and push. See [Contributing to a project by GitHub](https://docs.github.com/en/get-started/exploring-projects-on-github/contributing-to-a-project). 

Then, if you want to keep this alive and help tp maintain this map of the current state of the art of research in open-source software, send a pull request on GitHub, and it will be merged after testing.  

Knowledge of [BibTool](http://www.gerd-neugebauer.de/software/TeX/BibTool/en/) and [BibTeX Tidy](https://flamingtempura.github.io/bibtex-tidy/index.html) will not hurt. 

Avoid submitting duplicates or incomplete bibliometric entries. 

Process and clean a BibTeX file using `bibtool` with custom resource rules, if you have the skills. 

```sh
bibtool -v -d -k -r bibtoolrsc.rsc -F [in file] -o [out file]
``` 

## License
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/legalcode.txt)
Pick items as you wish. Cite or attribute if using any of the files as a whole. 
