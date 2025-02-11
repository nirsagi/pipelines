# Copyright 2018-2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import kfp
import kfp.compiler as compiler
import kfp.dsl as dsl
import os
import shutil
import subprocess
import sys
import zipfile
import tarfile
import tempfile
import unittest
import yaml

from kfp.dsl._component import component
from kfp.dsl import ContainerOp, pipeline
from kfp.dsl.types import Integer, InconsistentTypeException
from kubernetes.client import V1Toleration


class TestCompiler(unittest.TestCase):

  def test_operator_to_template(self):
    """Test converting operator to template"""

    from kubernetes import client as k8s_client

    with dsl.Pipeline('somename') as p:
      msg1 = dsl.PipelineParam('msg1')
      msg2 = dsl.PipelineParam('msg2', value='value2')
      json = dsl.PipelineParam('json')
      kind = dsl.PipelineParam('kind')
      op = dsl.ContainerOp(name='echo', image='image', command=['sh', '-c'],
                           arguments=['echo %s %s | tee /tmp/message.txt' % (msg1, msg2)],
                           file_outputs={'merged': '/tmp/message.txt'}) \
        .add_volume_mount(k8s_client.V1VolumeMount(
          mount_path='/secret/gcp-credentials',
          name='gcp-credentials')) \
        .add_env_variable(k8s_client.V1EnvVar(
          name='GOOGLE_APPLICATION_CREDENTIALS',
          value='/secret/gcp-credentials/user-gcp-sa.json'))
      res = dsl.ResourceOp(
        name="test-resource",
        k8s_resource=k8s_client.V1PersistentVolumeClaim(
          api_version="v1",
          kind=kind,
          metadata=k8s_client.V1ObjectMeta(
            name="resource"
          )
        ),
        attribute_outputs={"out": json}
      )
    golden_output = {
      'container': {
        'image': 'image',
        'args': [
          'echo {{inputs.parameters.msg1}} {{inputs.parameters.msg2}} | tee /tmp/message.txt'
        ],
        'command': ['sh', '-c'],
        'env': [
          {
            'name': 'GOOGLE_APPLICATION_CREDENTIALS',
            'value': '/secret/gcp-credentials/user-gcp-sa.json'
          }
        ],
        'volumeMounts':[
          {
            'mountPath': '/secret/gcp-credentials',
            'name': 'gcp-credentials',
          }
        ]
      },
      'inputs': {'parameters':
        [
          {'name': 'msg1'},
          {'name': 'msg2', 'value': 'value2'},
        ]},
      'name': 'echo',
      'outputs': {
        'parameters': [
          {'name': 'echo-merged',
           'valueFrom': {'path': '/tmp/message.txt'}
          }],
        'artifacts': [{
          'name': 'mlpipeline-ui-metadata',
          'path': '/mlpipeline-ui-metadata.json',
          'optional': True,
        },{
          'name': 'mlpipeline-metrics',
          'path': '/mlpipeline-metrics.json',
          'optional': True,
        }]
      }
    }
    res_output = {
      'inputs': {
        'parameters': [{
          'name': 'json'
        }, {
          'name': 'kind'
        }]
      },
      'name': 'test-resource',
      'outputs': {
        'parameters': [{
          'name': 'test-resource-manifest',
          'valueFrom': {
            'jsonPath': '{}'
          }
        }, {
          'name': 'test-resource-name',
          'valueFrom': {
            'jsonPath': '{.metadata.name}'
          }
        }, {
          'name': 'test-resource-out',
          'valueFrom': {
            'jsonPath': '{{inputs.parameters.json}}'
          }
        }]
      },
      'resource': {
        'action': 'create',
        'manifest': (
          "apiVersion: v1\n"
          "kind: '{{inputs.parameters.kind}}'\n"
          "metadata:\n"
          "  name: resource\n"
        )
      }
    }

    self.maxDiff = None
    self.assertEqual(golden_output, compiler._op_to_template._op_to_template(op))
    self.assertEqual(res_output, compiler._op_to_template._op_to_template(res))

  def _get_yaml_from_zip(self, zip_file):
    with zipfile.ZipFile(zip_file, 'r') as zip:
      with open(zip.extract(zip.namelist()[0]), 'r') as yaml_file:
        return yaml.safe_load(yaml_file)

  def _get_yaml_from_tar(self, tar_file):
    with tarfile.open(tar_file, 'r:gz') as tar:
      return yaml.safe_load(tar.extractfile(tar.getmembers()[0]))

  def test_basic_workflow(self):
    """Test compiling a basic workflow."""

    test_data_dir = os.path.join(os.path.dirname(__file__), 'testdata')
    sys.path.append(test_data_dir)
    import basic
    tmpdir = tempfile.mkdtemp()
    package_path = os.path.join(tmpdir, 'workflow.zip')
    try:
      compiler.Compiler().compile(basic.save_most_frequent_word, package_path)
      with open(os.path.join(test_data_dir, 'basic.yaml'), 'r') as f:
        golden = yaml.safe_load(f)
      compiled = self._get_yaml_from_zip(package_path)

      self.maxDiff = None
      # Comment next line for generating golden yaml.
      self.assertEqual(golden, compiled)
    finally:
      # Replace next line with commented line for gathering golden yaml.
      shutil.rmtree(tmpdir)
      # print(tmpdir)

  def test_composing_workflow(self):
    """Test compiling a simple workflow, and a bigger one composed from the simple one."""

    test_data_dir = os.path.join(os.path.dirname(__file__), 'testdata')
    sys.path.append(test_data_dir)
    import compose
    tmpdir = tempfile.mkdtemp()
    try:
      # First make sure the simple pipeline can be compiled.
      simple_package_path = os.path.join(tmpdir, 'simple.zip')
      compiler.Compiler().compile(compose.save_most_frequent_word, simple_package_path)

      # Then make sure the composed pipeline can be compiled and also compare with golden.
      compose_package_path = os.path.join(tmpdir, 'compose.zip')
      compiler.Compiler().compile(compose.download_save_most_frequent_word, compose_package_path)
      with open(os.path.join(test_data_dir, 'compose.yaml'), 'r') as f:
        golden = yaml.safe_load(f)
      compiled = self._get_yaml_from_zip(compose_package_path)

      self.maxDiff = None
      # Comment next line for generating golden yaml.
      self.assertEqual(golden, compiled)
    finally:
      # Replace next line with commented line for gathering golden yaml.
      shutil.rmtree(tmpdir)
      # print(tmpdir)

  def test_package_compile(self):
    """Test compiling python packages."""

    test_data_dir = os.path.join(os.path.dirname(__file__), 'testdata')
    test_package_dir = os.path.join(test_data_dir, 'testpackage')
    tmpdir = tempfile.mkdtemp()
    cwd = os.getcwd()
    try:
      os.chdir(test_package_dir)
      subprocess.check_call(['python3', 'setup.py', 'sdist', '--format=gztar', '-d', tmpdir])
      package_path = os.path.join(tmpdir, 'testsample-0.1.tar.gz')
      target_zip = os.path.join(tmpdir, 'compose.zip')
      subprocess.check_call([
          'dsl-compile', '--package', package_path, '--namespace', 'mypipeline',
          '--output', target_zip, '--function', 'download_save_most_frequent_word'])
      with open(os.path.join(test_data_dir, 'compose.yaml'), 'r') as f:
        golden = yaml.safe_load(f)
      compiled = self._get_yaml_from_zip(target_zip)

      self.maxDiff = None
      self.assertEqual(golden, compiled)
    finally:
      shutil.rmtree(tmpdir)
      os.chdir(cwd)

  def _test_py_compile_zip(self, file_base_name):
    test_data_dir = os.path.join(os.path.dirname(__file__), 'testdata')
    py_file = os.path.join(test_data_dir, file_base_name + '.py')
    tmpdir = tempfile.mkdtemp()
    try:
      target_zip = os.path.join(tmpdir, file_base_name + '.zip')
      subprocess.check_call([
          'dsl-compile', '--py', py_file, '--output', target_zip])
      with open(os.path.join(test_data_dir, file_base_name + '.yaml'), 'r') as f:
        golden = yaml.safe_load(f)
      compiled = self._get_yaml_from_zip(target_zip)

      self.maxDiff = None
      self.assertEqual(golden, compiled)
    finally:
      shutil.rmtree(tmpdir)

  def _test_py_compile_targz(self, file_base_name):
    test_data_dir = os.path.join(os.path.dirname(__file__), 'testdata')
    py_file = os.path.join(test_data_dir, file_base_name + '.py')
    tmpdir = tempfile.mkdtemp()
    try:
      target_tar = os.path.join(tmpdir, file_base_name + '.tar.gz')
      subprocess.check_call([
          'dsl-compile', '--py', py_file, '--output', target_tar])
      with open(os.path.join(test_data_dir, file_base_name + '.yaml'), 'r') as f:
        golden = yaml.safe_load(f)
      compiled = self._get_yaml_from_tar(target_tar)
      self.maxDiff = None
      self.assertEqual(golden, compiled)
    finally:
      shutil.rmtree(tmpdir)

  def _test_py_compile_yaml(self, file_base_name):
    test_data_dir = os.path.join(os.path.dirname(__file__), 'testdata')
    py_file = os.path.join(test_data_dir, file_base_name + '.py')
    tmpdir = tempfile.mkdtemp()
    try:
      target_yaml = os.path.join(tmpdir, file_base_name + '-pipeline.yaml')
      subprocess.check_call([
          'dsl-compile', '--py', py_file, '--output', target_yaml])
      with open(os.path.join(test_data_dir, file_base_name + '.yaml'), 'r') as f:
        golden = yaml.safe_load(f)

      with open(os.path.join(test_data_dir, target_yaml), 'r') as f:
        compiled = yaml.safe_load(f)

      self.maxDiff = None
      self.assertEqual(golden, compiled)
    finally:
      shutil.rmtree(tmpdir)

  def test_py_compile_artifact_location(self):
    """Test configurable artifact location pipeline."""
    self._test_py_compile_yaml('artifact_location')

  def test_py_compile_basic(self):
    """Test basic sequential pipeline."""
    self._test_py_compile_zip('basic')

  def test_py_compile_with_sidecar(self):
    """Test pipeline with sidecar."""
    self._test_py_compile_yaml('sidecar')

  def test_py_compile_with_pipelineparams(self):
    """Test pipeline with multiple pipeline params."""
    self._test_py_compile_yaml('pipelineparams')

  def test_py_compile_condition(self):
    """Test a pipeline with conditions."""
    self._test_py_compile_zip('coin')

  def test_py_compile_immediate_value(self):
    """Test a pipeline with immediate value parameter."""
    self._test_py_compile_targz('immediate_value')

  def test_py_compile_default_value(self):
    """Test a pipeline with a parameter with default value."""
    self._test_py_compile_targz('default_value')

  def test_py_volume(self):
    """Test a pipeline with a volume and volume mount."""
    self._test_py_compile_yaml('volume')

  def test_py_retry(self):
    """Test retry functionality."""
    self._test_py_compile_yaml('retry')

  def test_py_image_pull_secret(self):
    """Test pipeline imagepullsecret."""
    self._test_py_compile_yaml('imagepullsecret')

  def test_py_timeout(self):
    """Test pipeline timeout."""
    self._test_py_compile_yaml('timeout')

  def test_py_recursive_do_while(self):
    """Test pipeline recursive."""
    self._test_py_compile_yaml('recursive_do_while')

  def test_py_recursive_while(self):
    """Test pipeline recursive."""
    self._test_py_compile_yaml('recursive_while')

  def test_py_resourceop_basic(self):
    """Test pipeline resourceop_basic."""
    self._test_py_compile_yaml('resourceop_basic')

  def test_py_volumeop_basic(self):
    """Test pipeline volumeop_basic."""
    self._test_py_compile_yaml('volumeop_basic')

  def test_py_volumeop_parallel(self):
    """Test pipeline volumeop_parallel."""
    self._test_py_compile_yaml('volumeop_parallel')

  def test_py_volumeop_dag(self):
    """Test pipeline volumeop_dag."""
    self._test_py_compile_yaml('volumeop_dag')

  def test_py_volume_snapshotop_sequential(self):
    """Test pipeline volume_snapshotop_sequential."""
    self._test_py_compile_yaml('volume_snapshotop_sequential')

  def test_py_volume_snapshotop_rokurl(self):
    """Test pipeline volumeop_sequential."""
    self._test_py_compile_yaml('volume_snapshotop_rokurl')

  def test_py_volumeop_sequential(self):
    """Test pipeline volumeop_sequential."""
    self._test_py_compile_yaml('volumeop_sequential')

  def test_py_param_substitutions(self):
    """Test pipeline param_substitutions."""
    self._test_py_compile_yaml('param_substitutions')

  def test_py_param_op_transform(self):
    """Test pipeline param_op_transform."""
    self._test_py_compile_yaml('param_op_transform')

  def test_type_checking_with_consistent_types(self):
    """Test type check pipeline parameters against component metadata."""
    @component
    def a_op(field_m: {'GCSPath': {'path_type': 'file', 'file_type':'tsv'}}, field_o: Integer()):
      return ContainerOp(
          name = 'operator a',
          image = 'gcr.io/ml-pipeline/component-b',
          arguments = [
              '--field-l', field_m,
              '--field-o', field_o,
          ],
      )

    @pipeline(
        name='p1',
        description='description1'
    )
    def my_pipeline(a: {'GCSPath': {'path_type':'file', 'file_type': 'tsv'}}='good', b: Integer()=12):
      a_op(field_m=a, field_o=b)

    test_data_dir = os.path.join(os.path.dirname(__file__), 'testdata')
    sys.path.append(test_data_dir)
    tmpdir = tempfile.mkdtemp()
    try:
      simple_package_path = os.path.join(tmpdir, 'simple.tar.gz')
      compiler.Compiler().compile(my_pipeline, simple_package_path, type_check=True)

    finally:
      shutil.rmtree(tmpdir)

  def test_type_checking_with_inconsistent_types(self):
    """Test type check pipeline parameters against component metadata."""
    @component
    def a_op(field_m: {'GCSPath': {'path_type': 'file', 'file_type':'tsv'}}, field_o: Integer()):
      return ContainerOp(
          name = 'operator a',
          image = 'gcr.io/ml-pipeline/component-b',
          arguments = [
              '--field-l', field_m,
              '--field-o', field_o,
          ],
      )

    @pipeline(
        name='p1',
        description='description1'
    )
    def my_pipeline(a: {'GCSPath': {'path_type':'file', 'file_type': 'csv'}}='good', b: Integer()=12):
      a_op(field_m=a, field_o=b)

    test_data_dir = os.path.join(os.path.dirname(__file__), 'testdata')
    sys.path.append(test_data_dir)
    tmpdir = tempfile.mkdtemp()
    try:
      simple_package_path = os.path.join(tmpdir, 'simple.tar.gz')
      with self.assertRaises(InconsistentTypeException):
        compiler.Compiler().compile(my_pipeline, simple_package_path, type_check=True)
      compiler.Compiler().compile(my_pipeline, simple_package_path, type_check=False)

    finally:
      shutil.rmtree(tmpdir)

  def test_type_checking_with_json_schema(self):
    """Test type check pipeline parameters against the json schema."""
    @component
    def a_op(field_m: {'GCRPath': {'openapi_schema_validator': {"type": "string", "pattern": "^.*gcr\\.io/.*$"}}}, field_o: 'Integer'):
      return ContainerOp(
          name = 'operator a',
          image = 'gcr.io/ml-pipeline/component-b',
          arguments = [
              '--field-l', field_m,
              '--field-o', field_o,
          ],
      )

    @pipeline(
        name='p1',
        description='description1'
    )
    def my_pipeline(a: {'GCRPath': {'openapi_schema_validator': {"type": "string", "pattern": "^.*gcr\\.io/.*$"}}}='good', b: 'Integer'=12):
      a_op(field_m=a, field_o=b)

    test_data_dir = os.path.join(os.path.dirname(__file__), 'testdata')
    sys.path.append(test_data_dir)
    tmpdir = tempfile.mkdtemp()
    try:
      simple_package_path = os.path.join(tmpdir, 'simple.tar.gz')
      import jsonschema
      with self.assertRaises(jsonschema.exceptions.ValidationError):
        compiler.Compiler().compile(my_pipeline, simple_package_path, type_check=True)

    finally:
      shutil.rmtree(tmpdir)

  def test_compile_pipeline_with_after(self):
    def op():
      return dsl.ContainerOp(
        name='Some component name',
        image='image'
      )

    @dsl.pipeline(name='Pipeline', description='')
    def pipeline():
      task1 = op()
      task2 = op().after(task1)

    compiler.Compiler()._compile(pipeline)

  def _test_op_to_template_yaml(self, ops, file_base_name):
    test_data_dir = os.path.join(os.path.dirname(__file__), 'testdata')
    target_yaml = os.path.join(test_data_dir, file_base_name + '.yaml')
    with open(target_yaml, 'r') as f:
      expected = yaml.safe_load(f)['spec']['templates'][0]

    compiled_template = compiler._op_to_template._op_to_template(ops)

    del compiled_template['name'], expected['name']
    del compiled_template['outputs']['parameters'][0]['name'], expected['outputs']['parameters'][0]['name']
    assert compiled_template == expected

  def test_tolerations(self):
    """Test a pipeline with a tolerations."""
    op1 = dsl.ContainerOp(
      name='download',
      image='busybox',
      command=['sh', '-c'],
      arguments=['sleep 10; wget localhost:5678 -O /tmp/results.txt'],
      file_outputs={'downloaded': '/tmp/results.txt'}) \
      .add_toleration(V1Toleration(
      effect='NoSchedule',
      key='gpu',
      operator='Equal',
      value='run'))

    self._test_op_to_template_yaml(op1, file_base_name='tolerations')

  def test_set_display_name(self):
    """Test a pipeline with a customized task names."""

    import kfp
    op1 = kfp.components.load_component_from_text(
      '''
name: Component name
implementation:
  container:
    image: busybox
'''
    )

    @dsl.pipeline()
    def some_pipeline():
      op1().set_display_name('Custom name')

    workflow_dict = kfp.compiler.Compiler()._compile(some_pipeline)
    template = workflow_dict['spec']['templates'][0]
    self.assertEqual(template['metadata']['annotations']['pipelines.kubeflow.org/task_display_name'], 'Custom name')


  def test_op_transformers(self):
    def some_op():
      return dsl.ContainerOp(
          name='sleep',
          image='busybox',
          command=['sleep 1'],
      )

    @dsl.pipeline(name='some_pipeline', description='')
    def some_pipeline():
      task1 = some_op()
      task2 = some_op()
      task3 = some_op()

      dsl.get_pipeline_conf().op_transformers.append(lambda op: op.set_retry(5))

    workflow_dict = compiler.Compiler()._compile(some_pipeline)
    for template in workflow_dict['spec']['templates']:
      container = template.get('container', None)
      if container:
        self.assertEqual(template['retryStrategy']['limit'], 5)

  def test_add_pod_env(self):
    self._test_py_compile_yaml('add_pod_env')

  def test_init_container(self):
    echo = dsl.UserContainer(
      name='echo',
      image='alpine:latest',
      command=['echo', 'bye'])

    @dsl.pipeline(name='InitContainer', description='A pipeline with init container.')
    def init_container_pipeline():
      dsl.ContainerOp(
        name='hello',
        image='alpine:latest',
        command=['echo', 'hello'],
        init_containers=[echo])

    workflow_dict = compiler.Compiler()._compile(init_container_pipeline)
    for template in workflow_dict['spec']['templates']:
      init_containers = template.get('initContainers', None)
      if init_containers:
        self.assertEqual(len(init_containers),1)
        init_container = init_containers[0]
        self.assertEqual(init_container, {'image':'alpine:latest', 'command': ['echo', 'bye'], 'name': 'echo'})

