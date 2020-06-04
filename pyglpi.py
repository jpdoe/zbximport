# Popis: Modul pro praci s GLPI API
# Puvodni autor: Predict & Truly Systems
# https://github.com/truly-systems/glpi-sdk-python/blob/master/glpi/glpi.py, class GLPI(object)
# Copyright 2017 Predict & Truly Systems All Rights Reserved.
# Puvodni licence: Apache License 2.0 https://spdx.org/licenses/Apache-2.0.html
#
# Modifikace: Zjednoduseni a prejmenovani GLPI tridy na GlpiConnector, pridani metod pro praci s API a export dat
# Autor: Jan Polák
# Licence: MIT https://spdx.org/licenses/MIT.html
# Copyright 2018 Jan Polák

import requests
import logging
import json

# nastaveni logovani - best practice
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class GlpiConnectorException(Exception):
    pass


class GlpiConnector:
    """ Trida pro GLPI connector """

    def __init__(self, url_api, app_token, user_token, session=None, proxies=None):
        """
        Parameters:
           url_api: URL serveru kde bezi GLPI
           app_token: GLPI aplikacni token (nastaveni API)
           user_token: Token uzivatele (Remote acces key)
           session: session z modulu request. Pokud by bylo potreba specialne nastavene
           proxies: moznost zadat proxy - {'http': 'http://user:pass@10.10.1.10:1111/'}
        """

        self.url = url_api
        self.app_token = app_token
        self.user_token = user_token
        self.session_token = None

        if session:
            self.session = session
        else:
            self.session = requests.Session()

        self.proxies = proxies

        # Kontroly
        if self.app_token is None:
            logger.exception("Nebyl specifkovan app_token pro API")
            raise GlpiConnectorException(
                "Je nutný GLPI API-Token(app_token) pro volání API"
            )

        if self.user_token is None:
            logger.exception("Nebyl specifkovan user_token pro API")
            raise GlpiConnectorException(
                "Je nutný GLPI User token(user_token) pro volání API"
            )

    def init_session(self):
        """ Navaze spojeni """

        # URL ve tvaru: http://glpi.example.com/apirest.php
        full_url = self.url + "/initSession"

        # Hlavicka dle dokumentace
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"user_token {self.user_token}",
            "App-Token": self.app_token,
        }
        logger.debug("Zahajuji spojeni")

        # GET pozadavek
        r = self.session.get(full_url, headers=headers)
        logger.debug(f"Status kod init_session: {str(r.status_code)}")

        # Vraceny status code
        if r.status_code == 200:
            self.session_token = r.json()["session_token"]
        elif r.status_code == 400:
            raise GlpiConnectorException(f"BAD REQUEST: {r.text}")
        elif r.status_code == 401:
            raise GlpiConnectorException(f"UNAUTHORIZED ACCES: {r.text}")
        else:
            raise GlpiConnectorException("ANOTHER PROBLEM")

    def kill_session(self):
        """ Ukonci spojeni """
        # URL ve tvaru: http://glpi.example.com/apirest.php
        full_url = self.url + "/killSession"

        # Hlavicka dle dokumentace
        headers = {
            "Content-Type": "application/json",
            "Session-Token": self.session_token,
            "App-Token": self.app_token,
        }

        logger.debug("Ukoncuji spojeni")

        # GET pozadavek
        r = requests.get(full_url, headers=headers)
        logger.debug(f"Status kod kill_session: {str(r.status_code)}")

        # Vraceny status code
        if r.status_code == 200:
            self.session_token = None
        elif r.status_code == 400:
            raise GlpiConnectorException(f"BAD REQUEST: {r.text}")
        else:
            raise GlpiConnectorException("Chyba '400 Bad Request' GlpiConnectoru")

    def get_session_token(self):
        """ Vraci ID sezeni """

        if self.session_token is not None:
            return self.session_token
        else:
            return "Nelze získat Session Token"

    def get_params(self):
        """ Vraci parametry connectoru """
        return [self.url, self.app_token, self.user_token]

    def do_request(self, command, payload=None):
        """ Pozadavek na API
            Parameters:
               command: prikaz pro API
               payload: obsah payload
        """

        # URL ve tvaru: http://glpi.example.com/apirest.php
        full_url = self.url + "/" + command + "/"

        headers = {
            "Content-Type": "application/json",
            "Session-Token": self.session_token,
            "App-Token": self.app_token,
        }

        logger.debug(f"Payload: {str(payload)}")

        if payload is None:
            response = requests.get(full_url, headers=headers, proxies=self.proxies)
        else:
            response = requests.get(
                full_url, headers=headers, params=payload, proxies=self.proxies
            )

        logger.debug(f"Status kod do_request: {str(response.status_code)}")

        if response.status_code == 200:
            return response
        elif response.status_code == 400:
            raise GlpiConnectorException(f"Bad request: {response.text}")
        elif response.status_code == 401:
            raise GlpiConnectorException(f"Unauthorized: {response.text}")
        else:
            raise GlpiConnectorException(f"FUBAR - FUBAR - FUBAR: {response.text}")

    def get_all_network_items(self):
        """ Vrati vsechny polozky v networks """

        payload_all = {"range": "0-99999", "expand_dropdowns": "true"}
        return self.do_request("networkequipment", payload_all).json()

    def get_item_network_ports(self, item_id):
        """ Vrati polozku vcetne portu
             Parameters:
                item_id: ID polozky k ziskani
        """

        payload_single_item = {"expand_dropdowns": "true", "with_networkports": "true"}
        return self.do_request(
            "networkequipment/" + str(item_id), payload_single_item
        ).json()

    def get_item_parameters(self, item_id):
        """ Vrati polozku s parametry v urcitem formatu
            Parameters:
                item_id: ID polozky k ziskani
        """

        network_item = self.get_item_network_ports(item_id)

        new_item = {}
        to_return = []

        # rozdeleni pro extrakci skupiny z "xxx > yyy"
        if ">" in str(network_item["groups_id"]):

            splitted = [x.strip() for x in network_item["groups_id"].split(">")]
            new_item["groups_id"] = splitted[0]
            new_item["sub_group_id"] = splitted[1]

        else:
            new_item["groups_id"] = str(network_item["groups_id"])
            new_item["sub_group_id"] = new_item["groups_id"]

        new_item["domains_id"] = str(network_item["domains_id"])
        new_item["date_mod"] = network_item["date_mod"]

        new_item["zbx_proxy"] = "zbx-" + str(network_item["networks_id"])
        new_item["networks_id"] = str(network_item["networks_id"])
        new_item["id"] = str(network_item["id"])
        # GLPI name
        new_item["name"] = str(network_item["name"])

        logger.debug(
            f"Polozka {network_item['name']}: {json.dumps(network_item, indent=4, separators=(',', ': '))}"
        )

        # ziskani IP adresy a FQDN - rozhodovani kvuli rozdilnemu umisteni udaju pro switch a ostatni veci

        # polozka je switch - nutne kvuli formatu JSONu

        for key in network_item["_networkports"].keys():

            multi_iface = 0
            if (key == "NetworkPortAlias") or (key == "NetworkPortEthernet"):
                for alias in network_item["_networkports"][key]:

                    ip_addr = str(alias["NetworkName"]["IPAddress"][0]["name"])

                    logger.debug(f"Mam IP adresu: {ip_addr}")

                    net_name = str(alias["NetworkName"]["name"])
                    logger.debug(f"Mam sit. jmeno: {net_name}")

                    if "None" in {ip_addr, net_name}:
                        logger.debug(f"Preskakuji: {net_name} -> chybeji udaje.")

                        continue

                    net_domain = str(alias["NetworkName"]["FQDN"]["fqdn"])
                    logger.debug(f"Mam domenu: {net_domain}")

                    new_item["host_name"] = net_name
                    new_item["dns_name"] = net_name + "." + net_domain
                    new_item["ip_addr"] = ip_addr
                    multi_iface += 1

                    # nutno pouzit .copy() jinak se pouziva reference na stejny slovnik
                    to_return.append(new_item.copy())

        for item in to_return:

            if multi_iface > 1:
                item["multi_interface"] = True
            else:
                item["multi_interface"] = False

        if len(to_return) == 1:
            return to_return[0]
        else:
            return to_return

    def construct_list(self, iter_dict, data_dict):
        """ Vytvori seznam polozek s parametry
                Parameters:
                    iter_dict: seznam se jmeny polozek k ziskani
                    data_dict: slovnik s daty
        """
        host_list = []
        for i in iter_dict:
            host = self.get_item_parameters(data_dict[i]["id"])

            if type(host) is dict:
                host_list.append(host.copy())

            if type(host) is list:
                host_list.extend(host.copy())

        return host_list
