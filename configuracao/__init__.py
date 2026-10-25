# Usa o PyMySQL (100% Python) no lugar do mysqlclient, que precisa compilar
# bibliotecas em C e falha na instalacao do Vercel.
import pymysql

# o Django 6 exige mysqlclient >= 2.2.1; o PyMySQL funciona igual, so
# precisamos "informar" uma versao compativel.
pymysql.version_info = (2, 2, 1, "final", 0)
pymysql.install_as_MySQLdb()
