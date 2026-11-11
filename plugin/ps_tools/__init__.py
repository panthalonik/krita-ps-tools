from krita import DockWidgetFactory, DockWidgetFactoryBase, Krita

from .extension import PSToolsExtension
from .props_docker import DOCKER_ID, PropertiesDocker
from .toolbox import DOCKER_ID as TOOLBOX_ID, ToolboxDocker

Krita.instance().addExtension(PSToolsExtension(Krita.instance()))
Krita.instance().addDockWidgetFactory(
    DockWidgetFactory(DOCKER_ID, DockWidgetFactoryBase.DockRight, PropertiesDocker))
Krita.instance().addDockWidgetFactory(
    DockWidgetFactory(TOOLBOX_ID, DockWidgetFactoryBase.DockLeft, ToolboxDocker))
