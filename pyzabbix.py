# Autor: lukecyca (class ZabbixAPI, ZabbixAPIException, ZabbixAPIObjectClass)
# Popis: Modul pro praci s Zabbix API
# Zdroj: https://github.com/lukecyca/pyzabbix
# Modifikace: Pridana moznost proxy, typograficke upravy
# Licence: LGPL 2.1 https://spdx.org/licenses/LGPL-2.1.html

# Autor: Jan Polák (zbytek)
# Popis: Funkce pro praci s Zabbix API
# Licence: MIT https://spdx.org/licenses/MIT.html
# Copyright 2018 Jan Polák

import logging
import requests
import json

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ZabbixAPIException(Exception):
    """ generic zabbix api exception
    code list:
         -32602 - Invalid params (eg already exists)
         -32500 - no permissions
    """

    pass


# Pridana moznost proxy
class ZabbixAPI(object):
    def __init__(
        self,
        server="http://localhost/zabbix",
        session=None,
        use_authenticate=False,
        timeout=None,
        proxies=None,
    ):
        """
        Parameters:
            server: Base URI for zabbix web interface (omitting /api_jsonrpc.php)
            session: optional pre-configured requests.Session instance
            use_authenticate: Use old (Zabbix 1.8) style authentication
            timeout: optional connect and read timeout in seconds, default:
                     None (if you're using Requests >= 2.4 you can set it as tuple: "(connect, read)"
                     which is used to set individual connect and read timeouts.)
            proxies: Proxy authentication
        """

        if session:
            self.session = session
        else:
            self.session = requests.Session()

        # Default headers for all requests
        self.session.headers.update(
            {
                "Content-Type": "application/json-rpc",
                "User-Agent": "python/pyzabbix",
                "Cache-Control": "no-cache",
            }
        )

        self.use_authenticate = use_authenticate
        self.auth = ""
        self.id = 0

        self.timeout = timeout
        self.proxies = proxies
        self.url = server + "/api_jsonrpc.php"
        logger.debug(f"JSON-RPC Server Endpoint: {str(self.url)}")

    def login(self, user="", password=""):
        """Convenience method for calling user.authenticate and storing the resulting auth token
           for further commands.
           If use_authenticate is set, it uses the older (Zabbix 1.8) authentication command
           :param password: Password used to login into Zabbix
           :param user: Username used to login into Zabbix
        """

        # If we have an invalid auth token, we are not allowed to send a login
        # request. Clear it before trying.
        self.auth = ""
        if self.use_authenticate:
            self.auth = self.user.authenticate(user=user, password=password)
        else:
            self.auth = self.user.login(user=user, password=password)

    def check_authentication(self):
        """Convenience method for calling user.checkAuthentication of the current session"""
        return self.user.checkAuthentication(sessionid=self.auth)

    def confimport(self, confformat="", source="", rules=""):
        """Alias for configuration.import because it clashes with
           Python's import reserved keyword
           :param rules:
           :param source:
           :param confformat:
        """

        return self.do_request(
            method="configuration.import",
            params={"format": confformat, "source": source, "rules": rules},
        )["result"]

    def api_version(self):
        return self.apiinfo.version()

    def do_request(self, method, params=None):
        request_json = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self.id,
        }

        # We don't have to pass the auth token if asking for the apiinfo.version or user.checkAuthentication
        if (
            self.auth
            and method != "apiinfo.version"
            and method != "user.checkAuthentication"
        ):
            request_json["auth"] = self.auth

        logger.debug(
            f"Sending: {json.dumps(request_json, indent=4, separators=(',', ': '))}"
        )
        response = self.session.post(
            self.url,
            data=json.dumps(request_json),
            timeout=self.timeout,
            proxies=self.proxies,
        )
        logger.debug(f"Response Code: {str(response.status_code)}")

        # NOTE: Getting a 412 response code means the headers are not in the
        # list of allowed headers.
        response.raise_for_status()

        if not len(response.text):
            raise ZabbixAPIException("Received empty response")

        try:
            response_json = json.loads(response.text)
        except ValueError:
            raise ZabbixAPIException("Unable to parse json: %s" % response.text)
        logger.debug(
            f"Response Body: {json.dumps(response_json, indent=4, separators=(',', ': '))}"
        )

        self.id += 1

        if "error" in response_json:  # some exception
            if (
                "data" not in response_json["error"]
            ):  # some errors don't contain 'data': workaround for ZBX-9340
                response_json["error"]["data"] = "No data"
            msg = "Error {code}: {message}, {data}".format(
                code=response_json["error"]["code"],
                message=response_json["error"]["message"],
                data=response_json["error"]["data"],
            )
            raise ZabbixAPIException(msg, response_json["error"]["code"])

        return response_json

    def __getattr__(self, attr):
        """Dynamically create an object class (ie: host)"""
        return ZabbixAPIObjectClass(attr, self)


class ZabbixAPIObjectClass(object):
    def __init__(self, name, parent):
        self.name = name
        self.parent = parent

    def __getattr__(self, attr):
        """Dynamically create a method (ie: get)"""

        def new_fn(*args, **kwargs):
            if args and kwargs:
                raise TypeError("Found both args and kwargs")

            return self.parent.do_request(
                "{0}.{1}".format(self.name, attr), args or kwargs
            )["result"]

        return new_fn


def get_zabbix_items(item_type, zabbix_api):
    """
    Ziska ze Zabbixu polozku dle parametru
    Parameters:
       item_type: Typ polozky - proxy, template, hostgroup
       zabbix_api: API Zabbixu
    """

    if item_type == "proxy":

        all_zabbix_proxies_raw = []

        # Ziskani polozek
        try:
            all_zabbix_proxies_raw = zabbix_api.host.get(proxy_hosts="true")
        except ZabbixAPIException as error:
            logger.error(error)
            logger.error("Nejdou ziskat Zabbix proxy")

        return {proxy["host"]: proxy["hostid"] for proxy in all_zabbix_proxies_raw}

    elif item_type == "template":

        all_zabbix_template_raw = []

        # Ziskani polozek
        try:
            all_zabbix_template_raw = zabbix_api.template.get()
        except ZabbixAPIException as error:
            logger.error(error)
            logger.error("Nejdou ziskat Zabbix templates")

        return {
            template["host"]: template["templateid"]
            for template in all_zabbix_template_raw
        }

    elif item_type == "hostgroup":

        all_zabbix_hostgroup_raw = []

        # Ziskani polozek
        try:
            all_zabbix_hostgroup_raw = zabbix_api.hostgroup.get()
        except ZabbixAPIException as error:
            logger.error(error)
            logger.error("Nejdou ziskat Zabbix hostgroups")

        return {
            hostgroup["name"]: hostgroup["groupid"]
            for hostgroup in all_zabbix_hostgroup_raw
        }
    else:
        return "Spatny parametr pro get_zabbix_item"


class DictDiffer(object):
    """
    Zjisti rozdíly mezi dvěma slovníky:
    """

    def __init__(self, current_dict, past_dict):
        self.current_dict, self.past_dict = current_dict, past_dict
        self.set_current, self.set_past = (
            set(current_dict.keys()),
            set(past_dict.keys()),
        )

        self.intersect = self.set_current.intersection(self.set_past)

        self.tmp_changed = []
        self.tmp_unchanged = []

        for i in self.intersect:
            if self.past_dict[i] != self.current_dict[i]:
                self.tmp_changed.append(i)
            else:
                self.tmp_unchanged.append(i)

    def added(self):
        return tuple(self.set_current - self.intersect)

    def removed(self):
        return tuple(self.set_past - self.intersect)

    def changed(self):
        return tuple(self.tmp_changed)

    def unchanged(self):
        return tuple(self.tmp_unchanged)


def get_hosts_from_proxy(zabbix_api, proxy_id):
    """
    Ziska ze Zabbixu polozku dle parametru
    Parameters:
        proxy_id: ID proxy v Zabbixu
        zabbix_api: API Zabbixu
    """
    # ziskani vsech hostu s danou(aktualni) proxy
    zabbix_hosts = zabbix_api.host.get(proxyids=proxy_id)

    # extrakce polozek z Zabbix JSONu - kvuli porovnani - potrebuje jen nazvy
    zabbix_hosts_list = [i["host"] for i in zabbix_hosts]

    return zabbix_hosts_list


def delete_zbx_hosts(zabbix_api, list_to_delete):
    """
    Vymaze ze Zabbixu hosty
    Parameters:
        list_to_delete: Seznam hostu (name) k vymazani
        zabbix_api: API Zabbixu
    """
    if len(list_to_delete) == 0:
        logger.warning("Nic ke smazani")
        return

    logger.debug(f"Je nutne smazat hosty {str(list_to_delete)}")

    z_host_del = zabbix_api.host.get(
        output=["hostid"], filter={"host": list(list_to_delete)}
    )
    logger.debug(f"Host {str(z_host_del)}")

    try:
        # nutno mazat takto, jinak ZabbixApi dava parametry do tuple v request JSONu
        deleted_hosts = zabbix_api.do_request(
            "host.delete", params=[i["hostid"] for i in z_host_del]
        )

        logger.info(f"ID smazanych hostu {str(deleted_hosts)}")

        return str(deleted_hosts)

    except Exception as error:
        logger.exception(error)
        logger.exception(f"Problém při smazání {z_host_del}")
        logger.exception(f"Chyba pri mazani hostu")


def create_zbx_hosts(
    zabbix_api, list_of_host_params, zbx_groups, zbx_templates, zbx_proxies
):
    """
    Vytvori v Zabbixu polozky
    Parameters:
        zabbix_api: API Zabbixu
        list_of_host_params: seznam polozek s parametry
        zbx_groups: skupiny v Zabbixu - jméno:ID
        zbx_templates: šablony v Zabbixu -  jméno:ID
        zbx_proxies: proxy v Zabbixu - jméno:ID
    """
    logger.info(
        f"Je nutne vytvorit hosty {str([i['name'] for i in list_of_host_params])}"
    )
    created_hosts = []

    for item in list_of_host_params:

        # pokud je host_name nebo ip_addr "None" NEBO group_id nebo domains_id "0", preskoc polozku
        #
        if "None" in {item["dns_name"]} or "0" in {
            item["groups_id"],
            item["domains_id"],
        }:
            logger.warning(f"Preskakuji: {item['host_name']} -> chybeji udaje.")
            continue

        # kontrola name == hostname
        if (item["name"] != item["host_name"]) and (item["multi_interface"] == False):
            logger.warning(
                f"Preskakuji: {item['host_name']} -> hostname({item['host_name']}) != name({item['name']})"
            )
            continue

        # "vyroba" parametru pro vytvoreni polozky v Zabbixu

        # pokud je to UPS
        if "ups" in item["groups_id"]:
            parameters = {
                "host": item["host_name"],  # host_name
                "interfaces": [
                    {
                        "type": 2,
                        "main": 1,
                        "useip": 1,  # pouziti IP misto DNS
                        "ip": item["ip_addr"],
                        "dns": item["dns_name"],
                        "port": "161",
                        "bulk": "0",
                    }
                ],
                "macros": [{"macro": "{$SNMP_COMMUNITY}", "value": "public"}],
                "groups": [{"groupid": zbx_groups[item["groups_id"]]}],
                "templates": [{"templateid": zbx_templates[item["domains_id"]]}],
                "proxy_hostid": zbx_proxies[item["zbx_proxy"]],
                "inventory_mode": -1,
            }
        # neni UPS
        else:
            parameters = {
                "host": item["host_name"],
                "interfaces": [
                    {
                        "type": 1,
                        "main": 1,
                        "useip": 1,  # pouziti IP misto DNS
                        "ip": item["ip_addr"],
                        "dns": item["dns_name"],
                        "port": "10050",
                    }
                ],
                "groups": [{"groupid": zbx_groups[item["groups_id"]]}],
                "templates": [{"templateid": zbx_templates[item["domains_id"]]}],
                "proxy_hostid": zbx_proxies[item["zbx_proxy"]],
                "inventory_mode": -1,
            }
        logger.debug(f"Parametry noveho objektu: {str(parameters)}")

        # vytvoreni noveho hosta s parametry - viz vyse

        try:
            new_zabbix_host = zabbix_api.host.create(parameters)
        except Exception as error:
            logger.exception(error)
            logger.exception(f"Nesel vytvorit {item['host_name']}")
            new_zabbix_host = None
            logger.exception("Chyba pri vytvoreni hosta")

        if new_zabbix_host:
            logger.info(
                f"Vytvoren host {item['host_name']} s ID {str(new_zabbix_host['hostids'][0])}"
            )
        created_hosts.append(item["host_name"])

    return created_hosts


def get_params_zbx_host(zabbix_api, host_name, zbx_proxies):
    """"
    Ziskani a parsovani hostu ze Zabbixu
    Parameters:
        zabbix_api: API Zabbixu
        host_name: nazev hosta ktery se ma ziskat
        zbx_proxies: Proxy v Zabbixu
    """
    zabb_host = zabbix_api.host.get(
        selectParentTemplates=["name"],
        selectGroups=["name"],
        selectInterfaces=["dns", "port", "ip", "interfaceid"],
        filter={"host": host_name},
        output=["name", "proxy_hostid"],
    )

    host_name = zabb_host[0]["name"]
    dns_name = zabb_host[0]["interfaces"][0]["dns"]
    ip_addr = zabb_host[0]["interfaces"][0]["ip"]

    proxy = zabb_host[0]["proxy_hostid"]
    zbx_proxy = list(zbx_proxies.keys())[list(zbx_proxies.values()).index(proxy)]
    groups_id = zabb_host[0]["groups"][0]["name"]
    domains_id = zabb_host[0]["parentTemplates"][0]["name"]
    zbx_id = zabb_host[0]["hostid"]
    interface_id = zabb_host[0]["interfaces"][0]["interfaceid"]

    new_host = {
        "host_name": host_name,
        "dns_name": dns_name,
        "ip_addr": ip_addr,
        "zbx_proxy": zbx_proxy,
        "groups_id": groups_id,
        "domains_id": domains_id,
        "zbx_id": zbx_id,
        "zbx_interface_id": interface_id,
    }

    return new_host


def update_zbx_host(
    zabbix_api, glpi_host, zbx_host, zbx_groups, zbx_templates, zbx_proxies
):
    """"
    Zjisti rozdil mezi hostem v GLPI a Zabbixu a upravy zmenene polozky dle GLPI.
    Parameters:
        zabbix_api: API Zabbixu
        glpi_host: Parametry hosta v GLPI
        zbx_host: Parametry hosta v Zabbixu
        zbx_groups: skupiny v Zabbixu - jméno:ID
        zbx_templates: šablony v Zabbixu -  jméno:ID
        zbx_proxies: proxy v Zabbixu - jméno:ID
    """
    diff = DictDiffer(glpi_host, zbx_host)

    val = None
    differences = diff.changed()

    for diff in differences:

        if diff == "ip_addr":

            # zmena ip
            params = {
                "interfaceid": zbx_host["zbx_interface_id"],
                "ip": glpi_host["ip_addr"],
            }
            try:
                val = zabbix_api.hostinterface.update(params)
            except Exception as error:
                logger.exception(error)
                logger.exception(f"Problém při úpravě {glpi_host['name']}")
            # pass

        if diff == "zbx_proxy":

            # zmena proxy
            params = {
                "hostid": zbx_host["zbx_id"],
                "proxy_hostid": zbx_proxies[glpi_host["zbx_proxy"]],
            }
            try:
                val = zabbix_api.host.update(params)
            except Exception as error:
                logger.exception(error)
                logger.exception(f"Problém při úpravě {glpi_host['name']}")

        if diff == "groups_id":

            # zmena skupiny - nutno sestavit parameter pro groups stejne jako je v JSONu ze Zabbixu - staci ID skupiny
            params = {
                "hostid": zbx_host["zbx_id"],
                "groups": [{"groupid": zbx_groups[glpi_host["groups_id"]]}],
            }
            try:
                val = zabbix_api.host.update(params)
            except Exception as error:
                logger.exception(error)
                logger.exception(f"Problém při úpravě {glpi_host['name']}")

        if diff == "domains_id":

            params = {
                "hostid": zbx_host["zbx_id"],
                "templates": [{"templateid": zbx_templates[glpi_host["domains_id"]]}],
            }
            try:
                val = zabbix_api.host.update(params)
            except Exception as error:
                logger.exception(error)
                logger.exception(f"Problém při úpravě {glpi_host['name']}")

        if diff == "dns_name":

            params = {
                "interfaceid": zbx_host["zbx_interface_id"],
                "dns": glpi_host["dns_name"],
            }
            try:
                val = zabbix_api.hostinterface.update(params)
            except Exception as error:
                logger.exception(error)
                logger.exception(f"Problém při úpravě {glpi_host['name']}")

    return val
