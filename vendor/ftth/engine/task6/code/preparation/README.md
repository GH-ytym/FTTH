# Task 6 Data Preparation

These scripts prepare recognition and upstream planning data for the Task 6
layered planner. They are kept under Task 6 because their outputs are part of
the Task 6 input contract.

## Pipeline

1. `clean_recognition_data.py` filters the source recognition GeoPackage to the
   PM-assigned study extent and writes the cleaned recognition package.
2. `consolidate_cleaned_roads.py` removes access-candidate records and merges
   segmented roads into whole-road records in the cleaned recognition package.
3. `partition_buildings_by_imb.py` clips IMB Voronoi cells to recognized
   building-group polygons and writes the IMB building GeoJSON and review files.
4. `build_task6_production_input.py` combines Task 2/3/4 outputs and recognition
   outputs into `任务6/data/layered_planning_production_input.json`.

The normal commands are run from the project root:

```powershell
python .\任务6\code\preparation\clean_recognition_data.py
python .\任务6\code\preparation\consolidate_cleaned_roads.py
python .\任务6\code\preparation\partition_buildings_by_imb.py --groups <groups> --imb <imb> --output <output>
python .\任务6\code\preparation\build_task6_production_input.py
python .\任务6\code\plan_task6.py --input .\任务6\data\layered_planning_production_input.json --output .\任务6\output\layered_planning_production_plan.json
```

`geo_constraints.py` remains at the project root because it is a shared
geometry helper, not a Task 6 data-cleaning stage.
