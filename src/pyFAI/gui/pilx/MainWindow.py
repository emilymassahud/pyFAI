#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#    Project: Azimuthal integration
#             https://github.com/silx-kit/pyFAI
#
#    Copyright (C) 2023-2024 European Synchrotron Radiation Facility, Grenoble, France
#
#    Principal author:       Loïc Huder (loic.huder@ESRF.eu)
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#  .
#  The above copyright notice and this permission notice shall be included in
#  all copies or substantial portions of the Software.
#  .
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#  THE SOFTWARE.

"""Tool to visualize diffraction maps."""
from __future__ import annotations
__author__ = "Loïc Huder"
__contact__ = "loic.huder@ESRF.eu"
__license__ = "MIT"
__copyright__ = "European Synchrotron Radiation Facility, Grenoble, France"
__date__ = "12/03/2024"
__status__ = "development"


from typing import Tuple
import h5py
import logging
import os.path
from silx.gui import qt
from silx.gui.colors import Colormap
from silx.image.marchingsquares import find_contours


from .utils import (
    compute_radial_values,
    get_dataset,
    get_dataset_name,
    get_indices_from_values,
    get_radial_dataset,
)
from .widgets.DiffractionImagePlotWidget import DiffractionImagePlotWidget
from .widgets.IntegratedPatternPlotWidget import IntegratedPatternPlotWidget
from .widgets.MapPlotWidget import MapPlotWidget
from .widgets.TitleWidget import TitleWidget


class MainWindow(qt.QMainWindow):
    sigFileChanged = qt.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._file_name: str | None = None

        self.setWindowTitle("PyFAI-diffmap viewer")

        self._image_plot_widget = DiffractionImagePlotWidget(self)
        self._image_plot_widget.setDefaultColormap(
            Colormap("gray", normalization="log")
        )
        self._image_plot_widget.setKeepDataAspectRatio(True)
        self._image_plot_widget.plotClicked.connect(self.onMouseClickOnImage)

        self._map_plot_widget = MapPlotWidget(self)
        self._map_plot_widget.setDefaultColormap(
            Colormap("viridis", normalization="log")
        )
        self._map_plot_widget.plotClicked.connect(self.selectMapPoint)
        self.sigFileChanged.connect(self._map_plot_widget.onFileChange)

        self._integrated_plot_widget = IntegratedPatternPlotWidget(self)
        self._integrated_plot_widget.roi.sigRegionChanged.connect(self.onRoiEdition)
        self._integrated_plot_widget.roi.sigRangeChanged.connect(
            self.drawContoursOnImage
        )

        self._title_widget = TitleWidget(self)

        self._central_widget = qt.QWidget()
        layout = qt.QGridLayout(self._central_widget)
        layout.setSpacing(0)
        layout.addWidget(self._title_widget, 0, 0, 1, 2)
        layout.addWidget(self._image_plot_widget, 1, 0, 2, 1)
        layout.addWidget(self._map_plot_widget, 1, 1)
        layout.addWidget(self._integrated_plot_widget, 2, 1)
        self._central_widget.setLayout(layout)
        self.setCentralWidget(self._central_widget)

    def initData(self, file_name: str):
        self._file_name = os.path.abspath(file_name)
        self.sigFileChanged.emit(self._file_name)

        with h5py.File(self._file_name, "r") as h5file:
            map = get_dataset(h5file, "/entry_0000/pyFAI/result/intensity")[:, :, 0]
            pyFAI_config_as_str = get_dataset(
                h5file, "/entry_0000/pyFAI/configuration/data"
            )[()]
            radial_dset = get_radial_dataset(
                h5file, nxdata_path="/entry_0000/pyFAI/result"
            )
            delta_radial = (radial_dset[-1] - radial_dset[0]) / len(radial_dset)

        self._radial_matrix = compute_radial_values(pyFAI_config_as_str)
        self._delta_radial_over_2 = delta_radial / 2

        self._title_widget.setText(os.path.basename(file_name))
        self._map_plot_widget.setScatterData(map)
        # BUG: selectMapPoint(0, 0) does not work at first render cause the picking fails
        self.displayPatternAtIndex(0, 0)
        self.displayImageAtIndex(0, 0)
        self._map_plot_widget.addMarker(
            0, 0, color="black", symbol="o", legend="MAP_LOCATION"
        )

    def getRoiRadialRange(self) -> Tuple[float | None, float | None]:
        return self._integrated_plot_widget.roi.getRange()

    def displayPatternAtIndex(self, row: int, col: int):
        if self._file_name is None:
            return

        with h5py.File(self._file_name, "r") as h5file:
            radial_dset = get_radial_dataset(
                h5file, nxdata_path="/entry_0000/pyFAI/result"
            )
            radial = radial_dset[()]
            x_name = get_dataset_name(radial_dset)
            intensity_dset = get_dataset(h5file, "/entry_0000/pyFAI/result/intensity")
            pattern = intensity_dset[row, col, :]
            y_name = intensity_dset.attrs.get("long_name", "Intensity")

        self._integrated_plot_widget.addCurve(
            radial, pattern, legend="INTEGRATE", selectable=False
        )
        self._integrated_plot_widget.setGraphXLabel(x_name)
        self._integrated_plot_widget.setGraphYLabel(y_name)

    def displayImageAtIndex(self, row: int, col: int):
        if self._file_name is None:
            return

        with h5py.File(self._file_name, "r") as h5file:
            map_shape = get_dataset(h5file, "/entry_0000/pyFAI/result/intensity").shape
            try:
                image_dset = get_dataset(h5file, "/entry_0000/measurement/images_0001")
            except KeyError:
                image_link = h5file.get(
                    "/entry_0000/measurement/images_0001", getlink=True
                )
                error_msg = f"Cannot access diffraction images at {image_link}"
                logging.warning(error_msg)
                self.statusBar().showMessage(error_msg)
                return

            image = image_dset[col * map_shape[0] + row]

        self._image_plot_widget.setImageData(image)

    def selectMapPoint(self, x: float, y: float):
        indices = self._map_plot_widget.getImageIndices(x, y)
        if indices is None:
            return
        self.displayPatternAtIndex(row=indices.row, col=indices.col)
        self.displayImageAtIndex(row=indices.row, col=indices.col)
        pixel_center_coords = self._map_plot_widget.findCenterOfNearestPixel(x, y)
        self._map_plot_widget.addMarker(
            *pixel_center_coords, color="black", symbol="o", legend="MAP_LOCATION"
        )

    def onRoiEdition(self):
        v_min, v_max = self.getRoiRadialRange()
        if v_min is None or v_max is None:
            return

        self.displayAverageMap(v_min, v_max)

    def drawContoursOnImage(self):
        v_min, v_max = self.getRoiRadialRange()
        if v_min is None or v_max is None:
            return
        self._image_plot_widget.clearCurves()

        min_contours = find_contours(self._radial_matrix, v_min)
        for i, contour in enumerate(min_contours):
            self._image_plot_widget.addContour(contour, legend=f"min_contour_{i}")

        center_contours = find_contours(
            self._radial_matrix, v_min + (v_max - v_min) / 2
        )
        for i, contour in enumerate(center_contours):
            self._image_plot_widget.addContour(
                contour, legend=f"center_contour_{i}", linestyle=":"
            )

        max_contours = find_contours(self._radial_matrix, v_max)
        for i, contour in enumerate(max_contours):
            self._image_plot_widget.addContour(
                contour,
                legend=f"max_contour_{i}",
            )

    def displayAverageMap(self, v_min: float, v_max: float):
        if self._file_name is None:
            return

        with h5py.File(self._file_name, "r") as h5file:
            radial = get_radial_dataset(h5file, nxdata_path="/entry_0000/pyFAI/result")[
                ()
            ]
            i_min, i_max = get_indices_from_values(v_min, v_max, radial)
            map_data = get_dataset(h5file, "/entry_0000/pyFAI/result/intensity")[
                :, :, i_min:i_max
            ].mean(axis=2)

        self._map_plot_widget.setScatterData(map_data)

    def onMouseClickOnImage(self, x: float, y: float):
        indices = self._image_plot_widget.getImageIndices(x, y)
        if indices is None:
            return
        radial_value = self._radial_matrix[indices.row, indices.col]
        self._integrated_plot_widget.roi.setRange(
            radial_value - self._delta_radial_over_2,
            radial_value + self._delta_radial_over_2,
        )
