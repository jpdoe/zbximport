#!/usr/bin/python3

# Popis: Skript pro export z GLPI a import do Zabbixu
# Autor: Jan Polák
# Licence: MIT https://spdx.org/licenses/MIT.html
# Copyright 2018 Jan Polák

import logging.handlers
import os
import datetime
import pathlib
import configparser

# GLPI API
import pyglpi

# Zabbix API + funkce
import pyzabbix

BASE_PATH = pathlib.Path(__file__).parent

# nastaveni parseru, povoleni klicu bez hodnot
CONFIG_FILE = "config.ini"
config = configparser.ConfigParser(allow_no_value=True)
config.read((BASE_PATH / CONFIG_FILE).resolve(), encoding="utf-8")

# GLPI server
PROD_URL = config["glpi-server"]["url"]
PROD_APP_TOKEN = config["glpi-server"]["app-token"]
PROD_USER_TOKEN = config["glpi-server"]["user-token"]

# Zabbix server
ZABBIX_SERVER = config["zabbix-server"]["url"]
ZABBIX_USER = config["zabbix-server"]["user"]
ZABBIX_PASSWORD = config["zabbix-server"]["password"]

# certifikat pro pripojeni
ZABBIX_CERT = (BASE_PATH / config["zabbix-server"]["cert-file"]).resolve()

# soubor pro indikaci posledniho importu
LAST_IMPORT_FILE = (BASE_PATH / config["misc"]["last-import-file"]).resolve()

# "Magicka" konstanta
LAST_IMPORT_FILE_MAGIC_TUPLE = (424_242, 424_242)

# Seznam proxy v GLPI - vygenerovan seznam z configu
PROXY_LIST = [i[0] for i in config.items("proxy-list")]

# nastaveni logovacich konstant
LOG_FILE = (BASE_PATH / config["logging"]["log-file"]).resolve()
LOG_BYTES = int(config["logging"]["file-max-bytes"])
LOG_COUNTS = int(config["logging"]["file-count"])
LOG_LEVEL = config["logging"]["log-level"]

# root logger
logger = logging.getLogger()
# logovani do souboru
handler = logging.handlers.RotatingFileHandler(LOG_FILE, "a", LOG_BYTES, LOG_COUNTS)
# nastaveni formatu logovani
formatter = logging.Formatter("%(asctime)s %(module)s %(levelname)-4s %(message)s")
# prirazeni
handler.setFormatter(formatter)
logger.addHandler(handler)

# nastaveni log levelu
if LOG_LEVEL.lower() == "info":
    logger.setLevel(logging.INFO)
elif LOG_LEVEL.lower() == "debug":
    logger.setLevel(logging.DEBUG)


# Slovnik pro roztridene polozky
global_no_sort = {}

# "Globalni" seznam
global_to_delete = []
global_to_create = []
global_temp_list = []
global_to_update = []

# Pro urceni celkoveho casu
start_time = datetime.datetime.now()
logger.debug("Start importu")

# Pokud neni pomocny soubor z posledniho importu, vytvori novy a nastavi posledni pristup s casem 1970-01-05 22:50:42
# Je to kvuli prvnimu importu, aby se importovalo vsechno
if os.path.isfile(LAST_IMPORT_FILE) is False:
    pathlib.Path(LAST_IMPORT_FILE).touch()
    os.utime(LAST_IMPORT_FILE, LAST_IMPORT_FILE_MAGIC_TUPLE)

##################################################################################################################
# GLPI export #########################
##################################################################################################################

# Connector pro pripojeni k GLPI
connector = pyglpi.GlpiConnector(PROD_URL, PROD_APP_TOKEN, PROD_USER_TOKEN)

# vytvoreni spojeni
logger.debug("Zahajuji spojeni do GLPI")
connector.init_session()

logger.debug(f"Session token: {str(connector.get_session_token())}")

all_devices = connector.get_all_network_items()
logger.debug("Ziskana zarizeni z GLPI")

# seznam hostu, kteri nejsou sablona, nejsou smazani a maji nastaveno proxy ze seznamu
# pokud bude vytvorena nová proxy, pridat nazev do config file
selected_devices = [
    i
    for i in all_devices
    if i["is_template"] != 1 and i["is_deleted"] != 1
    if i["networks_id"] in PROXY_LIST
]

logger.debug("Prochazim jednotliva zarizeni")

# vytvoreni slovniku proxy s hosty - dulezite je item:{}
proxies_with_hosts = {"zbx-" + item: {} for item in PROXY_LIST}

# pruchod seznamem zarizeni
for item in selected_devices:
    # zapis do globalniho seznamu vsech zarizeni
    global_no_sort[item["name"]] = {"id": item["id"], "date_mod": item["date_mod"]}

    # TODO pridat groups_id ????
    # zapis polozky - prefix "zbx-" je kvuli nazvu proxy v Zabbixu
    proxies_with_hosts["zbx-" + item["networks_id"]][item["name"]] = {
        "id": item["id"],
        "date_mod": item["date_mod"],
    }

# Ukonceni pripojeni do GLPI
connector.kill_session()

##################################################################################################################
# Zabbix import #########################
##################################################################################################################

# Vytvoreni API

zapi = pyzabbix.ZabbixAPI(ZABBIX_SERVER)
zapi.session.verify = ZABBIX_CERT

# Prihlaseni k API
zapi.login(ZABBIX_USER, ZABBIX_PASSWORD)

logger.debug("Prace se Zabbixem")

# ziskani dvojic "nazev:ID": proxy, skupiny, sablony
all_zabbix_proxies = pyzabbix.get_zabbix_items("proxy", zapi)
all_zabbix_groups = pyzabbix.get_zabbix_items("hostgroup", zapi)
all_zabbix_templates = pyzabbix.get_zabbix_items("template", zapi)

# Citace
created_hosts_counter = 0
deleted_hosts_counter = 0
updated_hosts_counter = 0

# Pripojeni do GLPI
connector = pyglpi.GlpiConnector(PROD_URL, PROD_APP_TOKEN, PROD_USER_TOKEN)
logger.debug("Zahajuji spojeni do GLPI")
connector.init_session()

# iterace pres jednotlive proxy s hosty
for glpi_proxy_name, glpi_proxy_hosts_ids in proxies_with_hosts.items():

    # kontrola, jestli je proxy z GLPI v aktualne ziskanych Zabbix proxy
    if glpi_proxy_name in all_zabbix_proxies:

        # ziskani ID aktualne iterovane proxy
        proxy_id = all_zabbix_proxies[glpi_proxy_name]

        # # ziskani vsech hostu s danou(aktualni) proxy = vraci seznam s jmeny
        zabbix_hosts_list = pyzabbix.get_hosts_from_proxy(zapi, proxy_id)

        # prunik(spolecne prvky) nazvu hostu v zabbixu a glpi
        intersect = set(glpi_proxy_hosts_ids).intersection(set(zabbix_hosts_list))

        # polozky co jsou v Zabbixu, ale nejsou v GLPI = vymazat ze Zabbixu
        to_be_deleted_keys = set(zabbix_hosts_list) - intersect

        # polozky co jsou v GLPI, ale nejsou v Zabbixu = vytvorit v Zabbixu
        to_be_created_keys = set(glpi_proxy_hosts_ids) - intersect

        # polozky co jsou v GLPI i v Zabbixu = overit datum zmeny a porovnat s datem posledniho importu
        to_be_same_keys = intersect

        # pokud je neco k odstraneni
        if to_be_deleted_keys:
            global_to_delete.extend(list(to_be_deleted_keys))

        # pokud je neco k vytvoreni
        if to_be_created_keys:
            global_to_create.extend(list(to_be_created_keys))

        # pokud je to stejne
        if to_be_same_keys:

            # pokud jsou stejne, kontroluji zmenu
            for host_name in to_be_same_keys:

                # cas posledni modifikace polozky, potrebne pro rozhodnuti, zda delat import
                item_last_mod_time = datetime.datetime.strptime(
                    glpi_proxy_hosts_ids[host_name]["date_mod"], "%Y-%m-%d %H:%M:%S"
                )

                # ziska cas posledni modifikace (EPOCH format v sekundach)
                last_import_file_mod_time = datetime.datetime.fromtimestamp(
                    os.path.getmtime(LAST_IMPORT_FILE)
                )

                if item_last_mod_time > last_import_file_mod_time:
                    global_to_update.append(host_name)
    else:
        logger.error(f"Proxy {glpi_proxy_name} neni v Zabbixu!")

# pro pripad, ze se zmeni proxy, pak je host v delete i create
changed_proxy_hosts = set(global_to_delete).intersection(set(global_to_create))

# pokud je zmena proxy
if changed_proxy_hosts:
    for host in changed_proxy_hosts:

        # vyjmout z delete a create (je v obou) a spravne pridat do update
        global_to_delete.remove(host)
        global_to_create.remove(host)
        global_to_update.append(host)

# hromadne delete - pro vsechny hosty ke smazani
if global_to_delete:

    # vytvori seznam multi interface int1---int2

    check_del_list = [i for i in global_to_delete if "---" in i]

    if len(check_del_list) > 0:

        # vybere jen prvni casti nazvu interface
        splitted = set([x.split("---")[0] for x in check_del_list])

        test = connector.construct_list(splitted, global_no_sort)

        for item in test:
            if item["host_name"] in global_to_delete:
                global_to_delete.remove(item["host_name"])

    # test, pokud neni nic ke smazani, tak smaze vsechno!!!
    if len(global_to_delete) > 0:

        try:
            removed_zbx_hosts = pyzabbix.delete_zbx_hosts(zapi, global_to_delete)
        except Exception as e:
            logger.error(f"Vyjimka: {e}")

        if removed_zbx_hosts:
            logger.info(f"--DEL-- Polozky odstraneny: {str(global_to_delete)}")
            logger.debug(f"Vracene ID: {str(removed_zbx_hosts)} ")
            deleted_hosts_counter += len(global_to_delete)

# hromadne create - pro vsechny hosty k vytvoreni
if global_to_create:

    # ziskani parametru vsech hostu ze seznamu
    global_temp_list = connector.construct_list(global_to_create, global_no_sort)

    # vytvoreni hostu z listu
    if len(global_temp_list) > 0:
        try:
            added_zbx_hosts = pyzabbix.create_zbx_hosts(
                zabbix_api=zapi,
                list_of_host_params=global_temp_list,
                zbx_groups=all_zabbix_groups,
                zbx_templates=all_zabbix_templates,
                zbx_proxies=all_zabbix_proxies,
            )
        except Exception as e:
            logger.error(f"Vyjimka: {e}")

        if added_zbx_hosts:
            logger.info(f"--ADD-- Polozky vytvoreny: {str(added_zbx_hosts)}")
            created_hosts_counter += len(added_zbx_hosts)

# hromadne update - pro vsechny hosty k zmene
if global_to_update:

    for host_name in global_to_update:
        # ziskani parametru hosta - pro provedeni zmeny
        # z GLPI
        try:
            glpi_item = connector.get_item_parameters(global_no_sort[host_name]["id"])
        except KeyError:
            logger.warning(
                f"Preskakuji: {host_name} -> nema spravnou strukturu portu! "
            )
            continue

        # ze Zabbixu
        zabbix_item = pyzabbix.get_params_zbx_host(zapi, host_name, all_zabbix_proxies)

        # zjisti co se zmenilo a provede update pomoci hodnoty z GLPI
        try:
            updated_zbx_host = pyzabbix.update_zbx_host(
                zabbix_api=zapi,
                glpi_host=glpi_item,
                zbx_host=zabbix_item,
                zbx_groups=all_zabbix_groups,
                zbx_templates=all_zabbix_templates,
                zbx_proxies=all_zabbix_proxies,
            )
        except Exception as e:
            logger.error(f"Vyjimka: {e}")

        # pokud se update povedl
        if updated_zbx_host:
            logger.info(f"--UPD-- Polozka upravena: {str(glpi_item)}")
            logger.debug(f"Vracene ID: {str(updated_zbx_host)}")
            updated_hosts_counter += 1

# ukonceni spojeni
logger.debug("Ukonceni spojeni")
connector.kill_session()

# Pokud se provedla nejaka akce (smazani, vytvoreni, uprava) "touchne" se soubor a bude mit aktualni cas posledni zmeny
if (created_hosts_counter or deleted_hosts_counter or updated_hosts_counter) != 0:
    pathlib.Path(LAST_IMPORT_FILE).touch()

    logger.info(f" Celkem vytvoreno hostu: {str(created_hosts_counter)}")
    logger.info(f" Celkem odstraneno hostu: {str(deleted_hosts_counter)}")
    logger.info(f" Celkem upravenych hostu: {str(updated_hosts_counter)}")
else:
    logger.info("Neprobehla zmena")

logger.info(f"Celkovy cas importu: {str(datetime.datetime.now() - start_time)}")
