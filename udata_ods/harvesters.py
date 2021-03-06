# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import html2text

from udata.harvest.backends.base import BaseBackend
from udata.harvest.exceptions import HarvestSkipException
from udata.models import License, Resource


class OdsBackend(BaseBackend):
    display_name = 'OpenDataSoft'
    verify_ssl = False

    LICENSES = {
        "Open Database License (ODbL)": "odc-odbl",
        "Licence Ouverte (Etalab)": "fr-lo",
        "Licence ouverte / Open Licence": "fr-lo",
        "CC BY-SA": "cc-by-sa",
        "Public Domain": "other-pd"
    }

    FORMATS = {
        'csv': ('CSV', 'csv', 'text/csv'),
        'geojson': ('GeoJSON', 'json', 'application/vnd.geo+json'),
        'json': ('JSON', 'json', 'application/json'),
        'shp': ('Shapefile', 'shp', None),
    }

    @property
    def source_url(self):
        return self.source.url.rstrip('/')

    @property
    def api_url(self):
        return "{0}/api/datasets/1.0/search/".format(self.source_url)

    def explore_url(self, dataset_id):
        return "{0}/explore/dataset/{1}/".format(self.source_url, dataset_id)

    def download_url(self, dataset_id, format):
        return ("{0}download?format={1}&timezone=Europe/Berlin"
                "&use_labels_for_header=true"
                ).format(self.explore_url(dataset_id), format)

    def export_url(self, dataset_id):
        return "{0}?tab=export".format(self.explore_url(dataset_id))

    def initialize(self):
        count = 0
        nhits = None

        def should_fetch():
            if nhits is None:
                return True
            max_value = min(nhits, self.max_items) if self.max_items else nhits
            return count < max_value

        while should_fetch():
            response = self.get(self.api_url, params={
                "start": count,
                "rows": 50,
                "interopmetas": 'true',
            })
            response.raise_for_status()
            data = response.json()
            nhits = data["nhits"]
            for dataset in data["datasets"]:
                count += 1
                self.add_item(dataset["datasetid"], dataset=dataset)

    def process(self, item):
        ods_dataset = item.kwargs["dataset"]
        dataset_id = ods_dataset["datasetid"]
        ods_metadata = ods_dataset["metas"]
        ods_interopmetas = ods_dataset.get('interop_metas', {})

        if not ods_dataset.get('has_records'):
            msg = 'Dataset {datasetid} has no record'.format(**ods_dataset)
            raise HarvestSkipException(msg)

        # TODO: This behavior should be enabled with an option
        if 'inspire' in ods_interopmetas:
            msg = 'Dataset {datasetid} has INSPIRE metadata'
            raise HarvestSkipException(msg.format(**ods_dataset))

        dataset = self.get_dataset(item.remote_id)

        dataset.title = ods_metadata['title']
        dataset.frequency = "unknown"
        description = ods_metadata.get("description", '').strip()
        description = html2text.html2text(description.strip('\n').strip(),
                                          bodywidth=0)
        dataset.description = description.strip().strip('\n').strip()
        dataset.private = False

        tags = set()
        if "keyword" in ods_metadata:
            if isinstance(ods_metadata['keyword'], list):
                tags |= set(ods_metadata['keyword'])
            else:
                tags.add(ods_metadata['keyword'])

        if "theme" in ods_metadata:
            if isinstance(ods_metadata["theme"], list):
                for theme in ods_metadata["theme"]:
                    tags.update([t.strip().lower() for t in theme.split(",")])
            else:
                themes = ods_metadata["theme"].split(",")
                tags.update([t.strip().lower() for t in themes])

        dataset.tags = list(tags)

        # Detect license
        default_license = dataset.license or License.default()
        license_id = ods_metadata.get('license')
        dataset.license = License.guess(license_id,
                                        self.LICENSES.get(license_id),
                                        default=default_license)

        dataset.resources = []

        self.process_resources(dataset, ods_dataset, ('csv', 'json'))

        if 'geo' in ods_dataset['features']:
            self.process_resources(dataset, ods_dataset, ('geojson', 'shp'))

        dataset.extras["ods:url"] = self.explore_url(dataset_id)
        if "references" in ods_metadata:
            dataset.extras["ods:references"] = ods_metadata["references"]
        dataset.extras["ods:has_records"] = ods_dataset["has_records"]

        return dataset

    def process_resources(self, dataset, data, formats):
        dataset_id = data["datasetid"]
        ods_metadata = data["metas"]
        description = self.description_from_fields(data['fields'])
        for format in formats:
            label, udata_format, mime = self.FORMATS[format]
            resource = Resource(
                title='Export au format {0}'.format(label),
                description=description,
                filetype='remote',
                url=self.download_url(dataset_id, format),
                format=udata_format,
                mime=mime)
            resource.modified = ods_metadata["modified"]
            dataset.resources.append(resource)

    def description_from_fields(self, fields):
        '''Build a resource description/schema from ODS API fields'''
        if not fields:
            return

        out = ''
        for field in fields:
            out += '- *{label}*: {name}[{type}]'.format(**field)
            if field.get('description'):
                out += ' {description}'.format(**field)
            out += '\n'
        return out
