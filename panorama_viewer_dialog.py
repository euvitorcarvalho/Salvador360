# -*- coding: utf-8 -*-

"""
Para alterar o nome do plugin:
1. No arquivo metadata.txt, altere a linha "name=Salvador 360°" para "name=Seu Novo Nome"

2. Na linha 118, altere o código "self.setWindowTitle("Salvador 360°")" para "self.setWindowTitle("Seu Novo Nome")"

3.Na linha 161, altere o código "self.setWindowTitle("Salvador 360°")" para "self.setWindowTitle("Seu Novo Nome")"

4. Altere o nome da pasta do plugin (opcional, mas recomendado para evitar confusão).
"""

import os
import json
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
from time import time

from qgis.utils import iface
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsFeatureRequest,
    QgsWkbTypes,
    QgsMapLayerProxyModel,
    QgsRasterLayer
)
from qgis.gui import QgsFileWidget

from PyQt5.QtWebKitWidgets import QWebView, QWebPage
from PyQt5 import QtCore
from PyQt5.QtWidgets import (
    QVBoxLayout, QWidget, QHBoxLayout, QGridLayout, QPushButton,
    QMessageBox, QProgressBar, QDockWidget, QComboBox, QMainWindow,
    QSizePolicy, QLabel, QFileDialog
)
from PyQt5.QtCore import QUrl, QThread

# --- Constantes Globais ---
# Nomes exatos das camadas que o plugin procura ou cria.
BAIRROS_LAYER_NAME = "Bairros (SEDUR)"
LOGRADOUROS_LAYER_NAME = "Logradouros (SEDUR)"
PONTOS_LAYER_NAME = "Pontos de Panorama"
ORTOIMAGEM_LAYER_NAME = "Ortoimagem Salvador (WMS)"
# Define a pasta base do plugin para encontrar arquivos (HTML, image.JPG, etc.)
base_folder = os.path.dirname(os.path.realpath(__file__))
# Define a porta do servidor web local
HOST, PORT = "", 8030

# --- Servidor Web Local ---

# Classe para silenciar os logs do servidor HTTP no console
class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

# Thread para rodar o servidor HTTP em segundo plano
class HttpDaemon(QThread):
    def __init__(self, parent, path):
        super(QThread, self).__init__()
        self.server_path = path

    def run(self):
        # Muda o diretório de trabalho do servidor para a pasta do plugin
        os.chdir(self.server_path)
        # Inicia o servidor HTTP
        self.server = HTTPServer((HOST, PORT), QuietHandler)
        self.server.serve_forever()

    def stop(self):
        # Encerra o servidor HTTP de forma limpa
        self.server.shutdown()
        self.server.socket.close()

# --- Gerenciador de Arquivos do Panorama ---

class GetPanorama(QWidget):
    def __init__(self, parent):
        super().__init__()
        self.main_app = parent # Referência à janela principal (PanoramaViewer)

    def get_pano_file(self, file_url, method):
        # Ponto de entrada para obter o arquivo
        if method == "copy":
            return self.copy_file(file_url)
        return False

    def copy_file(self, file_in):
        # Esta função copia o panorama (que pode estar em rede) 
        # para a pasta local do plugin com o nome 'image.JPG'.
        # Isso é necessário para que o servidor web local possa acessá-lo.
        self.main_app.pbar.setDisabled(False) # Ativa a barra de progresso
        file_out = os.path.join(base_folder, "image.JPG")
        try:
            file_stats = os.stat(file_in)
            size_b = file_stats.st_size
            chunk_size = 4096 # Lê o arquivo em pedaços de 4KB
            # Calcula o incremento da barra de progresso
            p_step = 100 / (size_b / chunk_size) if size_b > 0 else 0
            counter = 0
            # Abre o arquivo de origem (leitura binária) e destino (escrita binária)
            with open(file_in, "rb") as f_in, open(file_out, "wb") as f_out:
                while True:
                    piece = f_in.read(chunk_size)
                    if not piece:
                        break # Terminou de ler
                    f_out.write(piece)
                    counter += p_step
                    # Atualiza a barra de progresso e processa eventos da interface
                    self.main_app.pbar.setValue(int(counter))
                    QtCore.QCoreApplication.processEvents()
            self.main_app.pbar.setValue(100) # Garante que chegou a 100%
            return True
        except Exception as e:
            QMessageBox.critical(self.main_app, "Erro", f"Erro ao copiar o arquivo de imagem:\n{e}")
            return False
        finally:
            # Reseta e desativa a barra de progresso, independente de sucesso or falha
            self.main_app.pbar.setValue(0)
            self.main_app.pbar.setDisabled(True)

# --- Janela Principal (DockWidget) ---

class PanoramaViewerDialog(QDockWidget):
    # Esta é a "casca" principal do plugin, o painel que se encaixa no QGIS.
    def __init__(self, wrapper):
        QDockWidget.__init__(self)
        self.wrapper = wrapper # Referência à classe principal do plugin (run)
        self.setWindowTitle("Salvador 360°") # NOME DO PLUGIN
        # Cria e insere o widget principal (PanoramaViewer) dentro do Dock
        self.gv = PanoramaViewer(self)
        self.setWidget(self.gv)
        self.closeEvent = self.onDestroy # Define o que fazer ao fechar
        self.wrapper.plugin_is_opened = True

    def onDestroy(self, e):
        # Função de "limpeza" executada quando o plugin é fechado
        self.wrapper.plugin_is_opened = False
        if self.gv.httpd:
            self.gv.httpd.stop() # Para o servidor web
        try:
            # Desconecta o sinal de seleção do mapa para evitar erros
            iface.mapCanvas().selectionChanged.disconnect(self.gv.visualizar_panorama_selecionado)
        except TypeError:
            pass # Ignora erro se já estiver desconectado

# Classe para silenciar mensagens de console do JavaScript
class WebPage(QWebPage):
    def __init__(self, main_view):
        super().__init__()
        self.mv = main_view
    def javaScriptConsoleMessage(self, msg, line, source):
        pass

# --- Interface Principal e Lógica (QMainWindow) ---

class PanoramaViewer(QMainWindow):
    # Esta classe contém toda a interface (botões, comboboxes, visualizador) 
    # e a lógica de filtragem e exibição.
    def __init__(self, parent):
        super().__init__(parent=None)
        
        self.wrapper = parent
        self.setWindowTitle("Salvador 360°") # NOME DO PLUGIN
        self.setGeometry(800, 650, 1000, 750)

        self.httpd = None # Placeholder para o servidor
        self.current_panorama_layer = None # Armazena a camada de pontos ativa

        # --- Definição da Interface (Layout) ---
        centralWidget = QWidget()
        main_layout = QVBoxLayout(centralWidget)

        # Layout dos botões de adicionar camadas
        add_layers_layout = QHBoxLayout()
        self.btn_add_bairros = QPushButton("Carregar Bairros (SEDUR)")
        self.btn_add_ortoimagem = QPushButton("Carregar Ortoimagem (WMS)")
        self.btn_add_logradouros = QPushButton("Carregar Logradouros (SEDUR)")
        self.btn_add_pontos = QPushButton("Adicionar Camada de Pontos (SHP)")
        add_layers_layout.addWidget(self.btn_add_bairros)
        add_layers_layout.addWidget(self.btn_add_ortoimagem)
        add_layers_layout.addWidget(self.btn_add_logradouros)
        add_layers_layout.addWidget(self.btn_add_pontos)

        # Layout do visualizador web (Pannellum.js)
        browser_layout = QHBoxLayout()
        self.view = QWebView(self)
        sp = self.view.sizePolicy()
        sp.setVerticalPolicy(QSizePolicy.Expanding)
        self.view.setSizePolicy(sp)
        self.view.settings().setObjectCacheCapacities(0, 0, 0) # Desativa cache
        self.page = WebPage(self)
        self.view.setPage(self.page)
        browser_layout.addWidget(self.view)

        # Layout dos filtros (Comboboxes)
        grid_layout = QGridLayout()
        self.lbl_bairro_field = QLabel("Campo 'Nome do Bairro':")
        self.cmb_bairro_field = QComboBox(self) # Seleciona o CAMPO (ex: "nome")

        self.lbl_bairro_select = QLabel("1. Selecione o Bairro:")
        self.cmb_bairro_select = QComboBox(self) # Seleciona o VALOR (ex: "Pituba")
        
        self.lbl_logradouro_select = QLabel("2. Selecione o Logradouro (Codlog):")
        self.cmb_logradouro_select = QComboBox(self) # Seleciona o CODLOG

        self.btn_exibir_panorama = QPushButton("3. Exibir Panorama (do ponto selecionado)")
        self.pbar = QProgressBar(self) # Barra de progresso
        self.pbar.setDisabled(True)

        grid_layout.addWidget(self.lbl_bairro_field, 0, 0)
        grid_layout.addWidget(self.cmb_bairro_field, 0, 1)
        grid_layout.addWidget(self.lbl_bairro_select, 1, 0)
        grid_layout.addWidget(self.cmb_bairro_select, 1, 1)
        grid_layout.addWidget(self.lbl_logradouro_select, 2, 0)
        grid_layout.addWidget(self.cmb_logradouro_select, 2, 1)

        # Adiciona os layouts à janela principal
        main_layout.addLayout(add_layers_layout)
        main_layout.addLayout(browser_layout)
        main_layout.addLayout(grid_layout)
        main_layout.addWidget(self.btn_exibir_panorama)
        main_layout.addWidget(self.pbar)
        self.setCentralWidget(centralWidget)

        # --- Conexões (Sinais e Slots) ---
        self.btn_add_bairros.clicked.connect(self.add_bairros_layer)
        self.btn_add_ortoimagem.clicked.connect(self.add_ortoimagem_layer)
        self.btn_add_logradouros.clicked.connect(self.add_logradouros_layer)
        self.btn_add_pontos.clicked.connect(self.add_pontos_layer)
        
        # Conexões da lógica de filtro em cascata
        self.cmb_bairro_field.currentIndexChanged.connect(self.popular_bairros_combobox)
        self.cmb_bairro_select.currentIndexChanged.connect(self.popular_logradouros_combobox)
        self.cmb_logradouro_select.currentIndexChanged.connect(self.exibir_pontos_do_logradouro)

        # Botão manual (agora redundante pela seleção, mas mantido)
        self.btn_exibir_panorama.clicked.connect(self.visualizar_panorama_selecionado)
        
        # Sinal do QGIS: atualiza combobox se a camada de bairros for adicionada
        QgsProject.instance().layersAdded.connect(self.atualizar_campos_bairro_se_necessario)
        
        # Sinal do QGIS: chama a visualização quando a seleção no mapa muda
        iface.mapCanvas().selectionChanged.connect(self.visualizar_panorama_selecionado)

        # Inicia o servidor web local
        self.httpd = HttpDaemon(self, base_folder)
        self.httpd.start()
        
        # Tenta popular os campos de bairro caso a camada já esteja no projeto
        self.atualizar_campos_bairro_se_necessario()

    # --- Funções de Lógica ---

    # Função utilitária para encontrar uma camada pelo nome
    def _find_layer_by_name(self, name):
        layers = QgsProject.instance().mapLayersByName(name)
        return layers[0] if layers else None

    # Adiciona a camada de Bairros (WFS)
    def add_bairros_layer(self):
        if self._find_layer_by_name(BAIRROS_LAYER_NAME):
            QMessageBox.information(self, "Informação", f"A camada '{BAIRROS_LAYER_NAME}' já está no projeto.")
            return
        uri = "http://geoserver.sedur.salvador.ba.gov.br:8080/geoserver/bairro_oficial/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=bairro_oficial:VM_BAIRRO_OFICIAL"
        layer = QgsVectorLayer(uri, BAIRROS_LAYER_NAME, "WFS")
        if not layer.isValid():
            QMessageBox.critical(self, "Erro de Conexão", "Não foi possível carregar a camada de bairros.")
        else:
            QgsProject.instance().addMapLayer(layer)

    # Adiciona a camada de Ortoimagem (WMS)
    def add_ortoimagem_layer(self):
        if self._find_layer_by_name(ORTOIMAGEM_LAYER_NAME):
            QMessageBox.information(self, "Informação", f"A camada '{ORTOIMAGEM_LAYER_NAME}' já está no projeto.")
            return
        wms_url = "http://mapeamento.salvador.ba.gov.br/wms"
        uri = f"crs=EPSG:31984&format=image/jpeg&layers=Ortoimagem_Salvador_2016_2017&styles=&url={wms_url}"
        layer = QgsRasterLayer(uri, ORTOIMAGEM_LAYER_NAME, "wms")
        if not layer.isValid():
            QMessageBox.critical(self, "Erro de Conexão WMS",
                                 "Não foi possível carregar a camada de Ortoimagem.\n\n"
                                 "Verifique a URL e sua conexão com a internet.\n"
                                 f"URL base tentada: {wms_url}")
        else:
            QgsProject.instance().addMapLayer(layer)

    # Adiciona a camada de Logradouros (WFS)
    def add_logradouros_layer(self):
        if self._find_layer_by_name(LOGRADOUROS_LAYER_NAME):
            QMessageBox.information(self, "Informação", f"A camada '{LOGRADOUROS_LAYER_NAME}' já está no projeto.")
            return
        uri = "http://geoserver.sedur.salvador.ba.gov.br:8080/geoserver/logradouros/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=logradouros:VM_LOGRADOURO"
        layer = QgsVectorLayer(uri, LOGRADOUROS_LAYER_NAME, "WFS")
        if not layer.isValid():
            QMessageBox.critical(self, "Erro de Conexão", "Não foi possível carregar a camada de logradouros.")
        else:
            QgsProject.instance().addMapLayer(layer)

    # Adiciona a camada de Pontos de Panorama (Shapefile local)
    def add_pontos_layer(self):
        if self._find_layer_by_name(PONTOS_LAYER_NAME):
            QMessageBox.information(self, "Informação", f"A camada '{PONTOS_LAYER_NAME}' já está no projeto.")
            self.current_panorama_layer = self._find_layer_by_name(PONTOS_LAYER_NAME)
            return
        
        # O SHP deve estar na subpasta 'data' do plugin
        shp_filename = "Panoramas_Jardim_armacao_Amostra.shp"
        shp_path = os.path.join(base_folder, "data", shp_filename)
        
        if not os.path.exists(shp_path):
             QMessageBox.critical(self, "Erro", f"O arquivo '{shp_filename}' não foi encontrado na pasta 'data' do plugin.")
             return

        layer = QgsVectorLayer(shp_path, PONTOS_LAYER_NAME, "ogr")
        if not layer.isValid():
            QMessageBox.critical(self, "Erro", f"Não foi possível carregar o arquivo shapefile:\n{shp_path}")
        else:
            QgsProject.instance().addMapLayer(layer)
            self.current_panorama_layer = layer # Define como a camada de panorama ativa
    
    # --- LÓGICA DE FILTRAGEM (Alterações principais) ---

    # 1. Atualiza o combobox de CAMPOS de bairro
    def atualizar_campos_bairro_se_necessario(self):
        bairro_layer = self._find_layer_by_name(BAIRROS_LAYER_NAME)
        if not bairro_layer:
            self.cmb_bairro_field.clear()
            self.cmb_bairro_select.clear()
            return

        # Popula o Cmb_bairro_field com todos os campos (colunas) da camada de bairros
        self.cmb_bairro_field.clear()
        self.cmb_bairro_field.addItems([field.name() for field in bairro_layer.fields()])
        
        # Tenta pré-selecionar o campo "nome"
        index = self.cmb_bairro_field.findText("nome", QtCore.Qt.MatchFixedString)
        if index >= 0:
             self.cmb_bairro_field.setCurrentIndex(index)
        
        # Chama a próxima função da cascata
        self.popular_bairros_combobox()

    # 2. Popula o combobox de VALORES de bairro
    def popular_bairros_combobox(self):
        bairro_layer = self._find_layer_by_name(BAIRROS_LAYER_NAME)
        field_name = self.cmb_bairro_field.currentText() # Pega o campo (ex: "nome")
        self.cmb_bairro_select.clear()
        self.cmb_bairro_select.addItem("-- Selecione um Bairro --")
        
        if not bairro_layer or not field_name:
            return
        
        try:
            # Pega todos os valores únicos do campo selecionado
            unique_values_raw = bairro_layer.uniqueValues(bairro_layer.fields().indexFromName(field_name))
            # Limpa (remove Nulos) e converte para string
            unique_values_str = {str(value) for value in unique_values_raw if value is not None}
            self.cmb_bairro_select.addItems(sorted(list(unique_values_str)))
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao ler nomes de bairros: {e}")

    # 3. Popula o combobox de Logradouros (baseado no Bairro)
    def popular_logradouros_combobox(self):
        bairro_nome = self.cmb_bairro_select.currentText() # Pega o valor (ex: "Pituba")
        self.cmb_logradouro_select.clear()
        self.cmb_logradouro_select.addItem("-- Selecione um Logradouro --")

        # Se nenhum bairro for selecionado, limpa o filtro da camada de pontos
        if not bairro_nome or bairro_nome == "-- Selecione um Bairro --":
            pontos_layer = self._find_layer_by_name(PONTOS_LAYER_NAME)
            if pontos_layer:
                pontos_layer.setSubsetString("") # Remove filtro
            return

        bairro_layer = self._find_layer_by_name(BAIRROS_LAYER_NAME)
        logradouro_layer = self._find_layer_by_name(LOGRADOUROS_LAYER_NAME)

        if not bairro_layer or not logradouro_layer:
            return
            
        # Encontra a geometria do bairro selecionado
        bairro_field = self.cmb_bairro_field.currentText()
        request = QgsFeatureRequest().setFilterExpression(f"\"{bairro_field}\" = '{bairro_nome}'")
        feature_bairro = next(bairro_layer.getFeatures(request), None)
        
        if not feature_bairro: return
        geom_bairro = feature_bairro.geometry()

        # --- Filtro Espacial ---
        # Cria uma requisição otimizada usando a Bounding Box do bairro
        request_logradouros = QgsFeatureRequest().setFilterRect(geom_bairro.boundingBox())
        
        codlogs = set() # Usamos 'set' para evitar duplicatas
        for f in logradouro_layer.getFeatures(request_logradouros):
            # Verificação final (e mais lenta) de interseção real
            if f.geometry().intersects(geom_bairro):
                codlogs.add(str(f["CODLOG"])) # Adiciona o CODLOG
        
        self.cmb_logradouro_select.addItems(sorted(list(codlogs)))

    # 4. Exibe os Pontos (baseado no Logradouro)
    def exibir_pontos_do_logradouro(self):
        codlog_selecionado = self.cmb_logradouro_select.currentText()
        pontos_layer = self._find_layer_by_name(PONTOS_LAYER_NAME)

        if not pontos_layer:
            return

        # Se nenhum logradouro for selecionado, limpa o filtro
        if not codlog_selecionado or codlog_selecionado == "-- Selecione um Logradouro --":
            pontos_layer.setSubsetString("")
            return
        
        self.current_panorama_layer = pontos_layer
        codlog_field_name = "codlog" # Nome do campo na camada de PONTOS
        
        # --- Filtro de Atributo ---
        # Aplica um filtro (WHERE) na camada de pontos
        filter_expression = f"\"{codlog_field_name}\" = '{codlog_selecionado}'"
        pontos_layer.setSubsetString(filter_expression)
        
        # Se encontrou pontos, dá zoom na extensão deles
        if pontos_layer.featureCount() > 0:
            iface.mapCanvas().setExtent(pontos_layer.extent())
            iface.mapCanvas().refresh()
        else:
             QMessageBox.information(self, "Resultado", f"Nenhum ponto de panorama encontrado para o codlog '{codlog_selecionado}'.")

    # --- Visualização do Panorama ---

    # Esta função é chamada QUANDO A SELEÇÃO NO MAPA MUDA
    def visualizar_panorama_selecionado(self, layer=None, feature_ids=None, a=None, b=None):
        # *args (layer, feature_ids, etc.) são ignorados, 
        # pois a função pega a seleção ativa diretamente.

        if self.current_panorama_layer is None:
            return # Não faz nada se a camada de pontos não foi definida
        
        # Verifica se a camada ativa no QGIS é a camada de pontos
        active_layer = iface.activeLayer()
        if not active_layer or active_layer.id() != self.current_panorama_layer.id():
            return

        # Pega as feições selecionadas
        selected_features = self.current_panorama_layer.selectedFeatures()
        
        # Só funciona se EXATAMENTE UMA feição estiver selecionada
        if len(selected_features) != 1: 
            self.view.setUrl(QUrl("about:blank")) # Limpa o visualizador
            return

        path_field_name = "path" # Campo que contém o caminho da imagem
        field_idx = self.current_panorama_layer.fields().indexFromName(path_field_name)
        if field_idx == -1:
            QMessageBox.critical(self, "Erro de Configuração", f"A coluna '{path_field_name}' não foi encontrada na camada de pontos.")
            return

        feature = selected_features[0]
        full_image_path = feature.attributes()[field_idx] # Pega o valor do campo 'path'
        
        if not full_image_path or not os.path.exists(full_image_path):
            QMessageBox.critical(self, "Imagem não encontrada", 
                                 f"O caminho da imagem especificado na tabela não foi encontrado:\n{full_image_path}")
            return
        
        # Se tudo deu certo, chama a função para carregar o visualizador
        self._load_panorama_view(full_image_path)

    def _load_panorama_view(self, image_path):
        # 1. Copia o arquivo de imagem para a pasta local (e mostra a barra de progresso)
        img_get = GetPanorama(self).get_pano_file(image_path, "copy")
        
        if img_get:
            # 2. Carrega o 'index_local.html' usando o servidor local
            # O 'time()' é adicionado para evitar problemas de cache do navegador
            url = f"http://localhost:{PORT}/index_local.html?v={time()}"
            self.view.load(QUrl(url))
        else:
            # Se a cópia falhar, carrega uma página de erro
            self.view.load(QUrl(f"http://localhost:{PORT}/index_error.html"))