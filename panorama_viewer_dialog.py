# -*- coding: utf-8 -*-
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

BAIRROS_LAYER_NAME = "Bairros (SEDUR)"
LOGRADOUROS_LAYER_NAME = "Logradouros (SEDUR)"
PONTOS_LAYER_NAME = "Pontos de Panorama"
ORTOIMAGEM_LAYER_NAME = "Ortoimagem Salvador (WMS)"
base_folder = os.path.dirname(os.path.realpath(__file__))
HOST, PORT = "", 8030

class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

class HttpDaemon(QThread):
    def __init__(self, parent, path):
        super(QThread, self).__init__()
        self.server_path = path

    def run(self):
        os.chdir(self.server_path)
        self.server = HTTPServer((HOST, PORT), QuietHandler)
        self.server.serve_forever()

    def stop(self):
        self.server.shutdown()
        self.server.socket.close()

class GetPanorama(QWidget):
    def __init__(self, parent):
        super().__init__()
        self.main_app = parent

    def get_pano_file(self, file_url, method):
        if method == "copy":
            return self.copy_file(file_url)
        return False

    def copy_file(self, file_in):
        self.main_app.pbar.setDisabled(False)
        file_out = os.path.join(base_folder, "image.JPG")
        try:
            file_stats = os.stat(file_in)
            size_b = file_stats.st_size
            chunk_size = 4096
            p_step = 100 / (size_b / chunk_size) if size_b > 0 else 0
            counter = 0
            with open(file_in, "rb") as f_in, open(file_out, "wb") as f_out:
                while True:
                    piece = f_in.read(chunk_size)
                    if not piece:
                        break
                    f_out.write(piece)
                    counter += p_step
                    self.main_app.pbar.setValue(int(counter))
                    QtCore.QCoreApplication.processEvents()
            self.main_app.pbar.setValue(100)
            return True
        except Exception as e:
            QMessageBox.critical(self.main_app, "Erro", f"Erro ao copiar o arquivo de imagem:\n{e}")
            return False
        finally:
            self.main_app.pbar.setValue(0)
            self.main_app.pbar.setDisabled(True)


class PanoramaViewerDialog(QDockWidget):
    def __init__(self, wrapper):
        QDockWidget.__init__(self)
        self.wrapper = wrapper
        self.setWindowTitle("Salvador 360°")
        self.gv = PanoramaViewer(self)
        self.setWidget(self.gv)
        self.closeEvent = self.onDestroy
        self.wrapper.plugin_is_opened = True

    def onDestroy(self, e):
        self.wrapper.plugin_is_opened = False
        if self.gv.httpd:
            self.gv.httpd.stop()
        try:
            iface.mapCanvas().selectionChanged.disconnect(self.gv.visualizar_panorama_selecionado)
        except TypeError:
            pass

class WebPage(QWebPage):
    def __init__(self, main_view):
        super().__init__()
        self.mv = main_view
    def javaScriptConsoleMessage(self, msg, line, source):
        pass

class PanoramaViewer(QMainWindow):
    def __init__(self, parent):
        super().__init__(parent=None)
        
        self.wrapper = parent
        self.setWindowTitle("Salvador 360°")
        self.setGeometry(800, 650, 1000, 750)

        self.httpd = None
        self.current_panorama_layer = None

        centralWidget = QWidget()
        main_layout = QVBoxLayout(centralWidget)

        add_layers_layout = QHBoxLayout()
        self.btn_add_bairros = QPushButton("Carregar Bairros (SEDUR)")
        self.btn_add_ortoimagem = QPushButton("Carregar Ortoimagem (WMS)")
        self.btn_add_logradouros = QPushButton("Carregar Logradouros (SEDUR)")
        self.btn_add_pontos = QPushButton("Adicionar Camada de Pontos (SHP)")
        add_layers_layout.addWidget(self.btn_add_bairros)
        add_layers_layout.addWidget(self.btn_add_ortoimagem)
        add_layers_layout.addWidget(self.btn_add_logradouros)
        add_layers_layout.addWidget(self.btn_add_pontos)

        browser_layout = QHBoxLayout()
        self.view = QWebView(self)
        sp = self.view.sizePolicy()
        sp.setVerticalPolicy(QSizePolicy.Expanding)
        self.view.setSizePolicy(sp)
        self.view.settings().setObjectCacheCapacities(0, 0, 0)
        self.page = WebPage(self)
        self.view.setPage(self.page)
        browser_layout.addWidget(self.view)

        grid_layout = QGridLayout()
        self.lbl_bairro_field = QLabel("Campo 'Nome do Bairro':")
        self.cmb_bairro_field = QComboBox(self)

        self.lbl_bairro_select = QLabel("1. Selecione o Bairro:")
        self.cmb_bairro_select = QComboBox(self)
        
        self.lbl_logradouro_select = QLabel("2. Selecione o Logradouro (Codlog):")
        self.cmb_logradouro_select = QComboBox(self)

        self.btn_exibir_panorama = QPushButton("3. Exibir Panorama (do ponto selecionado)")
        self.pbar = QProgressBar(self)
        self.pbar.setDisabled(True)

        grid_layout.addWidget(self.lbl_bairro_field, 0, 0)
        grid_layout.addWidget(self.cmb_bairro_field, 0, 1)
        grid_layout.addWidget(self.lbl_bairro_select, 1, 0)
        grid_layout.addWidget(self.cmb_bairro_select, 1, 1)
        grid_layout.addWidget(self.lbl_logradouro_select, 2, 0)
        grid_layout.addWidget(self.cmb_logradouro_select, 2, 1)

        main_layout.addLayout(add_layers_layout)
        main_layout.addLayout(browser_layout)
        main_layout.addLayout(grid_layout)
        main_layout.addWidget(self.btn_exibir_panorama)
        main_layout.addWidget(self.pbar)
        self.setCentralWidget(centralWidget)

        self.btn_add_bairros.clicked.connect(self.add_bairros_layer)
        self.btn_add_ortoimagem.clicked.connect(self.add_ortoimagem_layer)
        self.btn_add_logradouros.clicked.connect(self.add_logradouros_layer)
        self.btn_add_pontos.clicked.connect(self.add_pontos_layer)
        
        self.cmb_bairro_field.currentIndexChanged.connect(self.popular_bairros_combobox)
        self.cmb_bairro_select.currentIndexChanged.connect(self.popular_logradouros_combobox)
        self.cmb_logradouro_select.currentIndexChanged.connect(self.exibir_pontos_do_logradouro)

        self.btn_exibir_panorama.clicked.connect(self.visualizar_panorama_selecionado)
        
        QgsProject.instance().layersAdded.connect(self.atualizar_campos_bairro_se_necessario)
        
        iface.mapCanvas().selectionChanged.connect(self.visualizar_panorama_selecionado)

        self.httpd = HttpDaemon(self, base_folder)
        self.httpd.start()
        
        self.atualizar_campos_bairro_se_necessario()

    def _find_layer_by_name(self, name):
        layers = QgsProject.instance().mapLayersByName(name)
        return layers[0] if layers else None

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

    def add_pontos_layer(self):

        if self._find_layer_by_name(PONTOS_LAYER_NAME):
            QMessageBox.information(self, "Informação", f"A camada '{PONTOS_LAYER_NAME}' já está no projeto.")
            self.current_panorama_layer = self._find_layer_by_name(PONTOS_LAYER_NAME)
            return

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
            self.current_panorama_layer = layer
    
    def atualizar_campos_bairro_se_necessario(self):
        bairro_layer = self._find_layer_by_name(BAIRROS_LAYER_NAME)
        if not bairro_layer:
            self.cmb_bairro_field.clear()
            self.cmb_bairro_select.clear()
            return

        self.cmb_bairro_field.clear()
        self.cmb_bairro_field.addItems([field.name() for field in bairro_layer.fields()])
        
        index = self.cmb_bairro_field.findText("nome", QtCore.Qt.MatchFixedString)
        if index >= 0:
             self.cmb_bairro_field.setCurrentIndex(index)
        
        self.popular_bairros_combobox()

    def popular_bairros_combobox(self):
        bairro_layer = self._find_layer_by_name(BAIRROS_LAYER_NAME)
        field_name = self.cmb_bairro_field.currentText()
        self.cmb_bairro_select.clear()
        self.cmb_bairro_select.addItem("-- Selecione um Bairro --")
        
        if not bairro_layer or not field_name:
            return
        
        try:
            unique_values_raw = bairro_layer.uniqueValues(bairro_layer.fields().indexFromName(field_name))
            unique_values_str = {str(value) for value in unique_values_raw if value is not None}
            self.cmb_bairro_select.addItems(sorted(list(unique_values_str)))
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao ler nomes de bairros: {e}")

    def popular_logradouros_combobox(self):
        bairro_nome = self.cmb_bairro_select.currentText()
        self.cmb_logradouro_select.clear()
        self.cmb_logradouro_select.addItem("-- Selecione um Logradouro --")

        if not bairro_nome or bairro_nome == "-- Selecione um Bairro --":
            pontos_layer = self._find_layer_by_name(PONTOS_LAYER_NAME)
            if pontos_layer:
                pontos_layer.setSubsetString("")
            return

        bairro_layer = self._find_layer_by_name(BAIRROS_LAYER_NAME)
        logradouro_layer = self._find_layer_by_name(LOGRADOUROS_LAYER_NAME)

        if not bairro_layer or not logradouro_layer:
            return
            
        bairro_field = self.cmb_bairro_field.currentText()
        request = QgsFeatureRequest().setFilterExpression(f"\"{bairro_field}\" = '{bairro_nome}'")
        feature_bairro = next(bairro_layer.getFeatures(request), None)
        
        if not feature_bairro: return
        geom_bairro = feature_bairro.geometry()

        request_logradouros = QgsFeatureRequest().setFilterRect(geom_bairro.boundingBox())
        
        codlogs = set()
        for f in logradouro_layer.getFeatures(request_logradouros):
            if f.geometry().intersects(geom_bairro):
                codlogs.add(str(f["CODLOG"]))
        
        self.cmb_logradouro_select.addItems(sorted(list(codlogs)))

    def exibir_pontos_do_logradouro(self):
        codlog_selecionado = self.cmb_logradouro_select.currentText()
        pontos_layer = self._find_layer_by_name(PONTOS_LAYER_NAME)

        if not pontos_layer:
            return

        if not codlog_selecionado or codlog_selecionado == "-- Selecione um Logradouro --":
            pontos_layer.setSubsetString("")
            return
        
        self.current_panorama_layer = pontos_layer
        codlog_field_name = "codlog"
        
        filter_expression = f"\"{codlog_field_name}\" = '{codlog_selecionado}'"
        pontos_layer.setSubsetString(filter_expression)
        
        if pontos_layer.featureCount() > 0:
            iface.mapCanvas().setExtent(pontos_layer.extent())
            iface.mapCanvas().refresh()
        else:
             QMessageBox.information(self, "Resultado", f"Nenhum ponto de panorama encontrado para o codlog '{codlog_selecionado}'.")


    def visualizar_panorama_selecionado(self, layer=None, feature_ids=None, a=None, b=None):
        if self.current_panorama_layer is None:
            return
        
        active_layer = iface.activeLayer()
        if not active_layer or active_layer.id() != self.current_panorama_layer.id():
            return

        selected_features = self.current_panorama_layer.selectedFeatures()
        if len(selected_features) != 1: 
            self.view.setUrl(QUrl("about:blank"))
            return

        path_field_name = "path" 
        field_idx = self.current_panorama_layer.fields().indexFromName(path_field_name)
        if field_idx == -1:
            QMessageBox.critical(self, "Erro de Configuração", f"A coluna '{path_field_name}' não foi encontrada na camada de pontos.")
            return

        feature = selected_features[0]
        full_image_path = feature.attributes()[field_idx]
        
        if not full_image_path or not os.path.exists(full_image_path):
            QMessageBox.critical(self, "Imagem não encontrada", 
                                 f"O caminho da imagem especificado na tabela não foi encontrado:\n{full_image_path}")
            return
            
        self._load_panorama_view(full_image_path)

    def _load_panorama_view(self, image_path):
        img_get = GetPanorama(self).get_pano_file(image_path, "copy")
        
        if img_get:
            url = f"http://localhost:{PORT}/index_local.html?v={time()}"
            self.view.load(QUrl(url))
        else:
            self.view.load(QUrl(f"http://localhost:{PORT}/index_error.html"))