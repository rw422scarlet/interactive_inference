import xml.etree.ElementTree as ET
import numpy as np
import networkx as nx
import matplotlib.pyplot as plt

from shapely.geometry import Point, LineString, Polygon
from shapely.ops import unary_union
from shapely import speedups
speedups.disable()

from src.map_api.lanelet_layers import L2Point, L2Linestring, L2Polygon, Lanelet, Lane
from src.map_api.utils import LL2XYProjector
from src.map_api.utils import parse_node, parse_way, parse_relation
from src.data.geometry import get_cardinal_direction, dist_two_points
from src.visualization.map_vis import (
    get_way_styling, plot_ways, plot_lanelets, plot_lanes)

class MapReader:
    """ lanelet2 parser adapted from https://github.com/findaheng/lanelet2_parser """
    def __init__(self, cell_len=10):
        """
        Args:
            cell_len (float, optional): length of drivable cells. Defaults to 10.
        """
        self.cell_len = cell_len
        self.points = {}
        self.linestrings = {}
        self.polygons = {}
        self.lanelets = {}
        self.lanes = {} # concatenation of lanelets
        self.crosswalks = {}
        self.areas = {} # not implemented
        self.regulatory_elements = {} # note implemented
        
        # properties
        self._drivable_polygon = None
        self._cells = []
    
    @property
    def drivable_polygon(self):
        if self._drivable_polygon:
            return self._drivable_polygon
        
        lanelet_polygons = [l.polygon for l in self.lanelets.values() if l.subtype != "crosswalk"]
        self._drivable_polygon = unary_union(lanelet_polygons)
        return self._drivable_polygon
    
    @property
    def cells(self):
        if self._cells:
            return self._cells
        
        for lanelet in self.lanelets.values():
            for cell in lanelet.cells:
                self._cells.append((cell.polygon, cell.heading))
        return self._cells

    def match(self, x, y, target_lane_id=None, max_cells=5):
        """ Match point to map and return lane positions. 
            Left of target line is positive and right of target line is negative. 

        Args:
            x (float): target point x coordinate
            y (float): target point y coordinate
            target_lane_id (int, optional): lane id to be matched. default=None
            max_cells (int, optional): maximum number of cells adhead to return. default=5

        Returns:
            lane_id (int): matched lane id, Returns None if not matched
            cell_id (int): matched cell id.Returns None if not matched
            left_bound_dist (float): distance to cell left bound. Returns None if not matched
            right_bound_dist (float): distance to cell right bound. Returns None if not matched
            center_line_dist (float): distance to cell center line. Returns None if not matched
            cell_headings (np.array): array of left and right lookahead cell headings [max_cells, 2]. 
                nonpresent cells are filled with zeros.
        """
        def get_target_cell_id(p, cells):
            cell_id = None
            if target_lane_id is None:
                for cell_id, cell in enumerate(cells):
                    if cell.polygon.contains(p):
                        break
                    else:
                        cell_id = None
            
            if cell_id is None:
                dist_to_cells = []
                for cell_id, cell in enumerate(cells):
                    dist_to_cells.append(cell.polygon.exterior.distance(p))
                cell_id = np.argmin(dist_to_cells)
            return cell_id

        p = Point(x, y)

        search_lanes = self.lanes 
        if target_lane_id is not None:
            search_lanes = {target_lane_id: self.lanes[target_lane_id]}

        matched = False
        lane_id = None
        cell_id = None
        left_bound_dist = None
        right_bound_dist = None
        center_line_dist = None
        cell_headings = np.zeros((max_cells, 3)) * np.nan
        for lane_id, lane in search_lanes.items():
            is_contain = lane.polygon.contains(p)
            is_continue = True if target_lane_id is not None or is_contain else False

            if is_continue:
                num_cells = len(lane.cells)
                cell_id = get_target_cell_id(p, lane.cells)
                cell = lane.cells[cell_id]
                left_bound_coords = cell.left_bound.coords
                right_bound_coords = cell.right_bound.coords
                center_line_coords = cell.center_line.coords

                # compute directed lane distance
                left_bound_card = get_cardinal_direction(
                    left_bound_coords[0][0], left_bound_coords[0][1], cell.heading, x, y
                )
                right_bound_card = get_cardinal_direction(
                    right_bound_coords[0][0], right_bound_coords[0][1], cell.heading, x, y
                )
                center_line_card = get_cardinal_direction(
                    center_line_coords[0][0], center_line_coords[0][1], cell.heading, x, y
                )
                left_bound_dist = -np.sign(left_bound_card) * p.distance(cell.left_bound)
                right_bound_dist = np.sign(right_bound_card) * p.distance(cell.right_bound)
                center_line_dist = np.sign(center_line_card) * p.distance(cell.center_line)
                
                # compute lookahead cell headings
                last_cell_id = min(num_cells, cell_id + max_cells)
                cell_headings[:last_cell_id - cell_id] = np.array(
                    [[l.left_bound_heading, l.right_bound_heading, l.center_line_heading] 
                    for l in lane.cells[cell_id:last_cell_id]]
                )
                return lane_id, cell_id, left_bound_dist, right_bound_dist, center_line_dist, cell_headings
        if not matched:
            lane_id = None
        return lane_id, cell_id, left_bound_dist, right_bound_dist, center_line_dist, cell_headings
    
    def match_frenet(self, x, y, target_lane_id=None, max_cells=5):
        """ Match point to map in the frenet frame

        Args:
            x (float): target point x coordinate
            y (float): target point y coordinate
            target_lane_id (int, optional): lane id to be matched. default=None
            max_cells (int, optional): maximum number of cells adhead to return. default=5
        
        Returns:
            lane_id (int): matched lane id, Returns None if not matched
            psi_tan (int): heading of the center line tangent line. Returns None if not matched
            center_line_dist (float): distance to center line. Returns None if not matched
            left_bound_dist (float): distance to left bound. Returns None if not matched
            right_bound_dist (float): distance to right bound. Returns None if not matched
            wp_coords (np.array): coords of lookahead waypoints. Returns none if not matched
            wp_headings (np.array): headings of lookahead waypoints. Returns none if not matched
        """
        p = Point(x, y)
        
        search_lanes = self.lanes 
        if target_lane_id is not None:
            search_lanes = {target_lane_id: self.lanes[target_lane_id]}
        
        matched = False
        lane_id = None
        psi_tan = None
        centerline_dist = None
        left_bound_dist = None
        right_bound_dist = None
        wp_coords = None
        wp_headings = None
        for lane_id, lane in search_lanes.items():
            is_contain = lane.polygon.contains(p)
            is_continue = True if target_lane_id is not None or is_contain else False
            
            if is_continue:
                out = lane.get_frenet_coords(x, y)
                x_tan = out[0]
                y_tan = out[1]
                psi_tan = out[2]
                centerline_dist = out[3]
                left_bound_dist = out[4]
                right_bound_dist = out[5]
                
                # get far points
                farpoint_dists = self.cell_len * np.arange(1, max_cells + 1)
                
                wp_coords, wp_headings = lane.get_waypoints(x_tan, y_tan, farpoint_dists)
                return lane_id, psi_tan, centerline_dist, left_bound_dist, right_bound_dist, wp_coords, wp_headings
        
        if not matched:
            lane_id = None
        return lane_id, psi_tan, centerline_dist, left_bound_dist, right_bound_dist, wp_coords, wp_headings
        
    def plot(self, option="ways", annot=True, figsize=(15, 6)):
        """
        Args:
            option (str, optional): plotting options, 
                one of ["ways", "lanelets", "cells", "lanes"]. Defaults to "ways".
            annot (bool, optional): annotate map elements. Defaults to False.
            figsize (tuple, optional): figure size. Defaults to (15, 6).
        """
        fig, ax = plt.subplots(1, 1, figsize=figsize)
        
        if option == "ways":
            plot_ways(self, ax, annot=annot)
        elif option == "lanelets":
            plot_lanelets(self, ax, plot_cells=False, fill=True, annot=annot, alpha=0.4)
        elif option == "cells":
            plot_lanes(self, ax, plot_cells=True, annot=annot, alpha=0.4)
        elif option == "lanes":
            plot_lanes(self, ax, annot=annot, alpha=0.4)
        return fig, ax
    
    def parse(self, filepath, verbose=False):
        tree = ET.parse(filepath)
        root = tree.getroot()
        geo_projector = LL2XYProjector(0, 0)
        
        assert root.tag == "osm", f"{filepath} does not appear to be an OSM-XML file"
        
        for node in root.iter("node"):
            (id_, lon, lat, type_, subtype, ele, x, y) = parse_node(node)
            self._extract_point(id_, lon, lat, type_, subtype, ele, x, y, geo_projector)
            
        for way in root.iter("way"):
            (id_, ref_point_ids, area_tag, type_, subtype) = parse_way(way)
            if area_tag:
                self._extract_polygon(id_, ref_point_ids, type_, subtype)
            else:
                self._extract_linestring(id_, ref_point_ids, type_, subtype)
        
        for relation in root.iter("relation"):
            (id_, type_tag, subtype, region, 
            location, turn_direction, one_way, vehicle, 
            pedestrian, bicycle, fallback) = parse_relation(relation)
            if type_tag == "lanelet":
                self._extract_lanelet(id_, subtype, region, location, one_way, 
                    turn_direction, vehicle, pedestrian, bicycle, relation)
        
        self._extract_lanes()
        
        if verbose:
            print("found {} points, {} ways, {} lanelets, {} lanes".format(
                len(self.points), len(self.linestrings), len(self.lanelets), len(self.lanes)
            ))
    
    def get_way_dict(self):
        way_dict = []
        for i, (way_id, way) in enumerate(self.linestrings.items()):
            coords = list(way.linestring.coords)
            x = [c[0] for c in coords]
            y = [c[1] for c in coords]
            style_dict = get_way_styling(way.type_, way.subtype)
            dash = style_dict["dashes"] if "dashes" in style_dict.keys() else "solid"
            
            way_dict.append({
                "way_id": way_id,
                "type": way.type_,
                "subtype": way.subtype,
                "x": x,
                "y": y,
                "max_x": max(x),
                "max_y": max(y),
                "min_x": min(x),
                "min_y": min(y),
                "color": style_dict["color"],
                "linewidth": style_dict["linewidth"],
                "dash": dash
            })
        return way_dict

    def _extract_point(self, id_, lon, lat, type_, subtype, ele, x, y, geo_projector):
        x, y = geo_projector.latlon2xy(lat, lon)
        
        geo_point = Point(lon, lat)
        metric_point = Point(x, y)
        self.points[id_] = L2Point(id_, metric_point, geo_point, type_, subtype)
        
    def _extract_linestring(self, id_, ref_point_ids, type_, subtype):
        ref_points = [self.points[i] for i in ref_point_ids]
        ref_point_coords = [(p.point.x, p.point.y) for p in ref_points]
        linestring = LineString(ref_point_coords)
        self.linestrings[id_] = L2Linestring(id_, linestring, type_, subtype)
    
    def _extract_polygon(self, id_, ref_point_ids, type_, subtype):
        ref_points = [self.points[i] for i in ref_point_ids]
        ref_point_coords = [(p.point.x, p.point.y) for p in ref_points]
        polygon = Polygon(ref_point_coords)
        self.polygons[id_] = L2Polygon(id_, polygon, type_, subtype)
        
    def _extract_lanelet(self, id_, subtype, region, location, one_way, 
            turn_direction, vehicle, pedestrian, bicycle, relation):
        lanelet = Lanelet(id_, subtype, region, location, one_way, 
            turn_direction, vehicle, pedestrian, bicycle, 
            cell_len=self.cell_len)
        
        for member in relation.iter("member"):
            member_role = member.get("role")
            ref_id = int(member.get("ref"))
            
            # regulatory element handle
            if member_role == "regulatory_element":
                continue
            
            linestring = self.linestrings[ref_id]
            linestring.add_reference(id_)
            if member_role == "left":
                lanelet.left_bound = linestring
            elif member_role == "right":
                lanelet.right_bound = linestring
            elif member_role == "centerline":
                lanelet.centerline = linestring
            else:
                raise ValueError(f"Unknown member role {member_role} in lanelet with id={id_}")
        
        assert lanelet.left_bound and lanelet.right_bound, f"Lanelet with id={id_} missing bound(s)"
        lanelet._align_bounds()
        lanelet._find_centerline()
        
        if subtype == "crosswalk":
            self.crosswalks[id_] = lanelet
        else:
            self.lanelets[id_] = lanelet
    
    def _extract_lanes(self):    
        def is_connected(lanelet1, lanelet2):
            left_bound_linestring_1 = lanelet1.left_bound.linestring
            left_bound_linestring_2 = lanelet2.left_bound.linestring
            right_bound_linestring_1 = lanelet1.right_bound.linestring
            right_bound_linestring_2 = lanelet2.right_bound.linestring
            out = left_bound_linestring_1.intersects(left_bound_linestring_2)
            out = out and right_bound_linestring_1.intersects(right_bound_linestring_2)
            return out
        
        # build lanelet graph
        G = nx.Graph()
        lanelets = list(self.lanelets.values())
        for i in range(len(lanelets) - 1):
            for j in range(i + 1, len(lanelets)):
                node1_id, node1_val = lanelets[i].id_, lanelets[i]
                node2_id, node2_val = lanelets[j].id_, lanelets[j]
                if is_connected(node1_val, node2_val):
                        G.add_edge((node1_id, node1_val), (node2_id, node2_val))
                        G.add_edge((node2_id, node2_val), (node1_id, node1_val))
        
        # add reachable lanelets as lanes
        node_list = list(G.nodes)
        counter = 0
        while len(node_list) > 0:
            curr_node = node_list[0]
            connected_nodes = list(nx.descendants(G, curr_node))
            connected_nodes.append(curr_node)
            
            lanelets = [n[1] for n in connected_nodes]
            self.lanes[counter] = Lane(counter, lanelets, cell_len=self.cell_len)
            
            # remove all nodes found on the lane
            for n_id in connected_nodes:
                node_list.remove(n_id)
            counter += 1      
            
        # add adjacent lane id to lane property
        for i in range(len(self.lanes) - 1):
            for j in range(i + 1, len(self.lanes)):
                left_bound_linestr_1 = self.lanes[i].left_bound.linestring
                left_bound_linestr_2 = self.lanes[j].left_bound.linestring
                right_bound_linestr_1 = self.lanes[i].right_bound.linestring
                right_bound_linestr_2 = self.lanes[j].right_bound.linestring
                if left_bound_linestr_1.intersects(right_bound_linestr_2):
                    self.lanes[i].left_adjacent_lane_id.append(self.lanes[j].id_)
                    self.lanes[j].right_adjacent_lane_id.append(self.lanes[i].id_)
                elif right_bound_linestr_1.intersects(left_bound_linestr_2):
                    self.lanes[i].right_adjacent_lane_id.append(self.lanes[j].id_)
                    self.lanes[j].left_adjacent_lane_id.append(self.lanes[i].id_)