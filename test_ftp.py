from calendar import HTMLCalendar
from ftplib import FTP

FTP_HOST = "ftp.us.stackcp.com"  # <- HOST REAL DE STOICO
FTP_PORT = 21

FTP_USER = "certi@itaguigov.com"
FTP_PASS = "yUZ^CDNlTtOZ"

ftp = FTP()
ftp.connect(FTP_HOST, FTP_PORT, timeout=30)
ftp.login(FTP_USER, FTP_PASS)

ftp = FTP(FTP_HOST, timeout=30)
ftp.login(FTP_USER, FTP_PASS)

print("PWD:", ftp.pwd())
print("LISTA:", ftp.nlst())

ftp.quit()
