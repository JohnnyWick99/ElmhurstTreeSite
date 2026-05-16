# ElmhurstTreeSite
This is a basic website where you can search for different trees around Elmhurst's campus. 755/755 Trees have been successfully scraped and added to the database.

Site is being hosted on Amazon AWS EC2 instance @ http://18.223.16.129

NOTE: Site only works with HTTP - will not work if you use HTTPS

To make changes and view website on local machine:
1. Download the files
2. Download python and SQLite (Use terminal - winget(windows), brew(mac), apt(linux) - install)
3. Use terminal to navigate to project folder
4. Run command "python3 -m http.server 8000" (on mac)
5. Open localhost:8000 on a webbrowser

Could be added:
-Make "Elmhurst University Trees" title look better

-Page for adding new trees to the database with password

-Support for mobile version of the site

-Replace the QR codes at the bottom with a link to a new page thats a more printable version of tree info and QR code

-Add Leaflet map to top of index page with markers for every tree

-Add links to each Leaflet marker

-Add quickwiki facts for each different type of tree
