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
