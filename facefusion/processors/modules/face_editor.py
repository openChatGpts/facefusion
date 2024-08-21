from argparse import ArgumentParser
from typing import List

import cv2
import numpy
import scipy

import facefusion.jobs.job_manager
import facefusion.jobs.job_store
import facefusion.processors.core as processors
from facefusion import config, content_analyser, face_classifier, face_detector, face_landmarker, face_masker, face_recognizer, inference_manager, logger, process_manager, state_manager, wording
from facefusion.common_helper import create_float_metavar, map_float
from facefusion.download import conditional_download_hashes, conditional_download_sources
from facefusion.face_analyser import get_many_faces, get_one_face
from facefusion.face_helper import paste_back, scale_face_landmark_5, warp_face_by_face_landmark_5
from facefusion.face_masker import create_occlusion_mask, create_static_box_mask
from facefusion.face_selector import find_similar_faces, sort_and_filter_faces
from facefusion.face_store import get_reference_faces
from facefusion.filesystem import in_directory, is_image, is_video, resolve_relative_path, same_file_extension
from facefusion.processors import choices as processors_choices
from facefusion.processors.typing import FaceEditorInputs
from facefusion.program_helper import find_argument_group
from facefusion.thread_helper import thread_semaphore
from facefusion.typing import ApplyStateItem, Args, Expression, Face, FaceLandmark68, InferencePool, ModelOptions, ModelSet, MotionPoints, ProcessMode, QueuePayload, UpdateProgress, VisionFrame
from facefusion.vision import read_image, read_static_image, write_image

MODEL_SET : ModelSet =\
{
	'live_portrait':
	{
		'hashes':
		{
			'feature_extractor':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/live_portrait_feature_extractor.hash',
				'path': resolve_relative_path('../.assets/models/live_portrait_feature_extractor.hash')
			},
			'motion_extractor':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/live_portrait_motion_extractor.hash',
				'path': resolve_relative_path('../.assets/models/live_portrait_motion_extractor.hash')
			},
			'eye_retargeter':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/live_portrait_eye_retargeter.hash',
				'path': resolve_relative_path('../.assets/models/live_portrait_eye_retargeter.hash')
			},
			'lip_retargeter':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/live_portrait_lip_retargeter.hash',
				'path': resolve_relative_path('../.assets/models/live_portrait_lip_retargeter.hash')
			},
			'generator':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/live_portrait_generator.hash',
				'path': resolve_relative_path('../.assets/models/live_portrait_generator.hash')
			}
		},
		'sources':
		{
			'feature_extractor':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/live_portrait_feature_extractor.onnx',
				'path': resolve_relative_path('../.assets/models/live_portrait_feature_extractor.onnx')
			},
			'motion_extractor':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/live_portrait_motion_extractor.onnx',
				'path': resolve_relative_path('../.assets/models/live_portrait_motion_extractor.onnx')
			},
			'eye_retargeter':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/live_portrait_eye_retargeter.onnx',
				'path': resolve_relative_path('../.assets/models/live_portrait_eye_retargeter.onnx')
			},
			'lip_retargeter':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/live_portrait_lip_retargeter.onnx',
				'path': resolve_relative_path('../.assets/models/live_portrait_lip_retargeter.onnx')
			},
			'generator':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/live_portrait_generator.onnx',
				'path': resolve_relative_path('../.assets/models/live_portrait_generator.onnx')
			}
		},
		'template': 'ffhq_512',
		'size': (512, 512)
	}
}


def get_inference_pool() -> InferencePool:
	model_sources = get_model_options().get('sources')
	return inference_manager.get_inference_pool(__name__, model_sources)


def clear_inference_pool() -> None:
	inference_manager.clear_inference_pool(__name__)


def get_model_options() -> ModelOptions:
	return MODEL_SET[state_manager.get_item('face_editor_model')]


def register_args(program : ArgumentParser) -> None:
	group_processors = find_argument_group(program, 'processors')
	if group_processors:
		group_processors.add_argument('--face-editor-model', help = wording.get('help.face_editor_model'), default = config.get_str_value('processors.face_editor_model', 'live_portrait'), choices = processors_choices.face_editor_models)
		group_processors.add_argument('--face-editor-eyebrow-direction', help = wording.get('help.face_editor_eyebrow_direction'), type = float, default = config.get_float_value('processors.face_editor_eyebrow_direction', '0'), choices = processors_choices.face_editor_eyebrow_direction_range, metavar = create_float_metavar(processors_choices.face_editor_eyebrow_direction_range))
		group_processors.add_argument('--face-editor-eye-gaze-horizontal', help = wording.get('help.face_editor_eye_gaze_horizontal'), type = float, default = config.get_float_value('processors.face_editor_eye_gaze_horizontal', '0'), choices = processors_choices.face_editor_eye_gaze_horizontal_range, metavar = create_float_metavar(processors_choices.face_editor_eye_gaze_horizontal_range))
		group_processors.add_argument('--face-editor-eye-gaze-vertical', help = wording.get('help.face_editor_eye_gaze_vertical'), type = float, default = config.get_float_value('processors.face_editor_eye_gaze_vertical', '0'), choices = processors_choices.face_editor_eye_gaze_vertical_range, metavar = create_float_metavar(processors_choices.face_editor_eye_gaze_vertical_range))
		group_processors.add_argument('--face-editor-eye-open-ratio', help = wording.get('help.face_editor_eye_open_ratio'), type = float, default = config.get_float_value('processors.face_editor_eye_open_ratio', '0'), choices = processors_choices.face_editor_eye_open_ratio_range, metavar = create_float_metavar(processors_choices.face_editor_eye_open_ratio_range))
		group_processors.add_argument('--face-editor-lip-open-ratio', help = wording.get('help.face_editor_lip_open_ratio'), type = float, default = config.get_float_value('processors.face_editor_lip_open_ratio', '0'), choices = processors_choices.face_editor_lip_open_ratio_range, metavar = create_float_metavar(processors_choices.face_editor_lip_open_ratio_range))
		group_processors.add_argument('--face-editor-mouth-grim', help = wording.get('help.face_editor_mouth_grim'), type = float, default = config.get_float_value('processors.face_editor_mouth_grim', '0'), choices = processors_choices.face_editor_mouth_grim_range, metavar = create_float_metavar(processors_choices.face_editor_mouth_grim_range))
		group_processors.add_argument('--face-editor-mouth-pout', help = wording.get('help.face_editor_mouth_pout'), type = float, default = config.get_float_value('processors.face_editor_mouth_pout', '0'), choices = processors_choices.face_editor_mouth_pout_range, metavar = create_float_metavar(processors_choices.face_editor_mouth_pout_range))
		group_processors.add_argument('--face-editor-mouth-purse', help = wording.get('help.face_editor_mouth_purse'), type = float, default = config.get_float_value('processors.face_editor_mouth_purse', '0'), choices = processors_choices.face_editor_mouth_purse_range, metavar = create_float_metavar(processors_choices.face_editor_mouth_purse_range))
		group_processors.add_argument('--face-editor-mouth-smile', help = wording.get('help.face_editor_mouth_smile'), type = float, default = config.get_float_value('processors.face_editor_mouth_smile', '0'), choices = processors_choices.face_editor_mouth_smile_range, metavar = create_float_metavar(processors_choices.face_editor_mouth_smile_range))
		group_processors.add_argument('--face-editor-mouth-position-horizontal', help = wording.get('help.face_editor_mouth_position_horizontal'), type = float, default = config.get_float_value('processors.face_editor_mouth_position_horizontal', '0'), choices = processors_choices.face_editor_mouth_position_horizontal_range, metavar = create_float_metavar(processors_choices.face_editor_mouth_position_horizontal_range))
		group_processors.add_argument('--face-editor-mouth-position-vertical', help = wording.get('help.face_editor_mouth_position_vertical'), type = float, default = config.get_float_value('processors.face_editor_mouth_position_vertical', '0'), choices = processors_choices.face_editor_mouth_position_vertical_range, metavar = create_float_metavar(processors_choices.face_editor_mouth_position_vertical_range))
		facefusion.jobs.job_store.register_step_keys([ 'face_editor_model', 'face_editor_eyebrow_direction', 'face_editor_eye_gaze_horizontal', 'face_editor_eye_gaze_vertical', 'face_editor_eye_open_ratio', 'face_editor_lip_open_ratio', 'face_editor_mouth_grim', 'face_editor_mouth_pout', 'face_editor_mouth_purse', 'face_editor_mouth_smile', 'face_editor_mouth_position_horizontal', 'face_editor_mouth_position_vertical' ])


def apply_args(args : Args, apply_state_item : ApplyStateItem) -> None:
	apply_state_item('face_editor_model', args.get('face_editor_model'))
	apply_state_item('face_editor_eyebrow_direction', args.get('face_editor_eyebrow_direction'))
	apply_state_item('face_editor_eye_gaze_horizontal', args.get('face_editor_eye_gaze_horizontal'))
	apply_state_item('face_editor_eye_gaze_vertical', args.get('face_editor_eye_gaze_vertical'))
	apply_state_item('face_editor_eye_open_ratio', args.get('face_editor_eye_open_ratio'))
	apply_state_item('face_editor_lip_open_ratio', args.get('face_editor_lip_open_ratio'))
	apply_state_item('face_editor_mouth_grim', args.get('face_editor_mouth_grim'))
	apply_state_item('face_editor_mouth_pout', args.get('face_editor_mouth_pout'))
	apply_state_item('face_editor_mouth_purse', args.get('face_editor_mouth_purse'))
	apply_state_item('face_editor_mouth_smile', args.get('face_editor_mouth_smile'))
	apply_state_item('face_editor_mouth_position_horizontal', args.get('face_editor_mouth_position_horizontal'))
	apply_state_item('face_editor_mouth_position_vertical', args.get('face_editor_mouth_position_vertical'))


def pre_check() -> bool:
	download_directory_path = resolve_relative_path('../.assets/models')
	model_hashes = get_model_options().get('hashes')
	model_sources = get_model_options().get('sources')

	return conditional_download_hashes(download_directory_path, model_hashes) and conditional_download_sources(download_directory_path, model_sources)


def pre_process(mode : ProcessMode) -> bool:
	if mode in [ 'output', 'preview' ] and not is_image(state_manager.get_item('target_path')) and not is_video(state_manager.get_item('target_path')):
		logger.error(wording.get('choose_image_or_video_target') + wording.get('exclamation_mark'), __name__.upper())
		return False
	if mode == 'output' and not in_directory(state_manager.get_item('output_path')):
		logger.error(wording.get('specify_image_or_video_output') + wording.get('exclamation_mark'), __name__.upper())
		return False
	if mode == 'output' and not same_file_extension([ state_manager.get_item('target_path'), state_manager.get_item('output_path') ]):
		logger.error(wording.get('match_target_and_output_extension') + wording.get('exclamation_mark'), __name__.upper())
		return False
	return True


def post_process() -> None:
	read_static_image.cache_clear()
	if state_manager.get_item('video_memory_strategy') in [ 'strict', 'moderate' ]:
		clear_inference_pool()
	if state_manager.get_item('video_memory_strategy') == 'strict':
		content_analyser.clear_inference_pool()
		face_classifier.clear_inference_pool()
		face_detector.clear_inference_pool()
		face_landmarker.clear_inference_pool()
		face_masker.clear_inference_pool()
		face_recognizer.clear_inference_pool()


def edit_face(target_face : Face, temp_vision_frame : VisionFrame) -> VisionFrame:
	model_template = get_model_options().get('template')
	model_size = get_model_options().get('size')
	face_landmark_5 = scale_face_landmark_5(target_face.landmark_set.get('5/68'), 1.2)
	crop_vision_frame, affine_matrix = warp_face_by_face_landmark_5(temp_vision_frame, face_landmark_5, model_template, model_size)
	box_mask = create_static_box_mask(crop_vision_frame.shape[:2][::-1], state_manager.get_item('face_mask_blur'), (0, 0, 0, 0))
	crop_masks =\
	[
		box_mask
	]

	if 'occlusion' in state_manager.get_item('face_mask_types'):
		occlusion_mask = create_occlusion_mask(crop_vision_frame)
		crop_masks.append(occlusion_mask)
	crop_vision_frame = prepare_crop_frame(crop_vision_frame)
	crop_vision_frame = apply_edit(crop_vision_frame, target_face.landmark_set.get('68'))
	crop_vision_frame = normalize_crop_frame(crop_vision_frame)
	crop_mask = numpy.minimum.reduce(crop_masks).clip(0, 1)
	temp_vision_frame = paste_back(temp_vision_frame, crop_vision_frame, crop_mask, affine_matrix)
	return temp_vision_frame


def apply_edit(crop_vision_frame : VisionFrame, face_landmark_68 : FaceLandmark68) -> VisionFrame:
	feature_extractor = get_inference_pool().get('feature_extractor')
	motion_extractor = get_inference_pool().get('motion_extractor')
	generator = get_inference_pool().get('generator')

	with thread_semaphore():
		feature_volume = feature_extractor.run(None,
		{
			'input': crop_vision_frame
		})[0]

	with thread_semaphore():
		pitch, yaw, roll, scale, translation, expression, motion_points = motion_extractor.run(None,
		{
			'input': crop_vision_frame
		})

	rotation_matrix = scipy.spatial.transform.Rotation.from_euler('xyz', [ pitch, yaw, roll ], degrees = True).as_matrix()
	rotation_matrix = rotation_matrix.T.astype(numpy.float32)
	motion_points_transform = scale * (motion_points @ rotation_matrix + expression) + translation
	expression = edit_eye_gaze(expression)
	expression = edit_mouth_grim(expression)
	expression = edit_mouth_position(expression)
	expression = edit_mouth_pout(expression)
	expression = edit_mouth_purse(expression)
	expression = edit_mouth_smile(expression)
	expression = edit_eyebrow_direction(expression)
	motion_points_edit = motion_points @ rotation_matrix
	motion_points_edit += expression
	motion_points_edit *= scale
	motion_points_edit += translation
	motion_points_edit += edit_eye_open(motion_points_transform, face_landmark_68)
	motion_points_edit += edit_lip_open(motion_points_transform, face_landmark_68)

	with thread_semaphore():
		crop_vision_frame = generator.run(None,
		{
			'feature_volume': feature_volume,
			'target': motion_points_transform,
			'source': motion_points_edit
		})[0][0]

	return crop_vision_frame


def edit_eyebrow_direction(expression : Expression) -> Expression:
	face_editor_eyebrow = state_manager.get_item('face_editor_eyebrow_direction')

	if face_editor_eyebrow > 0:
		expression[0, 1, 1] += map_float(face_editor_eyebrow, -1, 1, -0.015, 0.015)
		expression[0, 2, 1] -= map_float(face_editor_eyebrow, -1, 1, -0.020, 0.020)
	else:
		expression[0, 1, 0] -= map_float(face_editor_eyebrow, -1, 1, -0.015, 0.015)
		expression[0, 2, 0] += map_float(face_editor_eyebrow, -1, 1, -0.020, 0.020)
		expression[0, 1, 1] += map_float(face_editor_eyebrow, -1, 1, -0.005, 0.005)
		expression[0, 2, 1] -= map_float(face_editor_eyebrow, -1, 1, -0.005, 0.005)
	return expression


def edit_eye_gaze(expression : Expression) -> Expression:
	face_editor_eye_gaze_horizontal = state_manager.get_item('face_editor_eye_gaze_horizontal')
	face_editor_eye_gaze_vertical = state_manager.get_item('face_editor_eye_gaze_vertical')

	if face_editor_eye_gaze_horizontal > 0:
		expression[0, 11, 0] += map_float(face_editor_eye_gaze_horizontal, -1, 1, -0.015, 0.015)
		expression[0, 15, 0] += map_float(face_editor_eye_gaze_horizontal, -1, 1, -0.020, 0.020)
	else:
		expression[0, 11, 0] += map_float(face_editor_eye_gaze_horizontal, -1, 1, -0.020, 0.020)
		expression[0, 15, 0] += map_float(face_editor_eye_gaze_horizontal, -1, 1, -0.015, 0.015)
	expression[0, 1, 1] += map_float(face_editor_eye_gaze_vertical, -1, 1, -0.0025, 0.0025)
	expression[0, 2, 1] -= map_float(face_editor_eye_gaze_vertical, -1, 1, -0.0025, 0.0025)
	expression[0, 11, 1] -= map_float(face_editor_eye_gaze_vertical, -1, 1, -0.010, 0.010)
	expression[0, 13, 1] -= map_float(face_editor_eye_gaze_vertical, -1, 1, -0.005, 0.005)
	expression[0, 15, 1] -= map_float(face_editor_eye_gaze_vertical, -1, 1, -0.010, 0.010)
	expression[0, 16, 1] -= map_float(face_editor_eye_gaze_vertical, -1, 1, -0.005, 0.005)
	return expression


def edit_eye_open(motion_points : MotionPoints, face_landmark_68 : FaceLandmark68) -> MotionPoints:
	eye_retargeter = get_inference_pool().get('eye_retargeter')
	face_editor_eye_open_ratio = state_manager.get_item('face_editor_eye_open_ratio')
	left_eye_ratio = calc_distance_ratio(face_landmark_68, 37, 40, 39, 36)
	right_eye_ratio = calc_distance_ratio(face_landmark_68, 43, 46, 45, 42)

	if face_editor_eye_open_ratio < 0:
		close_eye_motion_points = numpy.concatenate([ motion_points.ravel(), [ left_eye_ratio, right_eye_ratio, 0.0 ] ])
		close_eye_motion_points = close_eye_motion_points.reshape(1, -1).astype(numpy.float32)

		with thread_semaphore():
			close_eye_motion_points = eye_retargeter.run(None,
			{
				'input': close_eye_motion_points
			})[0]

		eye_motion_points = close_eye_motion_points * face_editor_eye_open_ratio * -1
	else:
		open_eye_motion_points = numpy.concatenate([ motion_points.ravel(), [ left_eye_ratio, right_eye_ratio, 0.8 ] ])
		open_eye_motion_points = open_eye_motion_points.reshape(1, -1).astype(numpy.float32)

		with thread_semaphore():
			open_eye_motion_points = eye_retargeter.run(None,
			{
				'input': open_eye_motion_points
			})[0]

		eye_motion_points = open_eye_motion_points * face_editor_eye_open_ratio
	eye_motion_points = eye_motion_points.reshape(-1, 21, 3)
	return eye_motion_points


def edit_lip_open(motion_points : MotionPoints, face_landmark_68 : FaceLandmark68) -> MotionPoints:
	lip_retargeter = get_inference_pool().get('lip_retargeter')
	face_editor_lip_open_ratio = state_manager.get_item('face_editor_lip_open_ratio')
	lip_ratio = calc_distance_ratio(face_landmark_68, 62, 66, 54, 48)

	if face_editor_lip_open_ratio < 0:
		close_lip_motion_points = numpy.concatenate([ motion_points.ravel(), [ lip_ratio, 0.0 ] ])
		close_lip_motion_points = close_lip_motion_points.reshape(1, -1).astype(numpy.float32)

		with thread_semaphore():
			close_lip_motion_points = lip_retargeter.run(None,
			{
				'input': close_lip_motion_points
			})[0]

		lip_motion_points = close_lip_motion_points * face_editor_lip_open_ratio * -1
	else:
		open_lip_motion_points = numpy.concatenate([ motion_points.ravel(), [ lip_ratio, 1.3 ] ])
		open_lip_motion_points = open_lip_motion_points.reshape(1, -1).astype(numpy.float32)

		with thread_semaphore():
			open_lip_motion_points = lip_retargeter.run(None,
			{
				'input': open_lip_motion_points
			})[0]

		lip_motion_points = open_lip_motion_points * face_editor_lip_open_ratio
	lip_motion_points = lip_motion_points.reshape(-1, 21, 3)
	return lip_motion_points


def edit_mouth_grim(expression : Expression) -> Expression:
	face_editor_mouth_grim = state_manager.get_item('face_editor_mouth_grim')
	if face_editor_mouth_grim > 0:
		expression[0, 17, 2] -= map_float(face_editor_mouth_grim, -1, 1, -0.005, 0.005)
		expression[0, 19, 2] += map_float(face_editor_mouth_grim, -1, 1, -0.01, 0.01)
		expression[0, 20, 1] -= map_float(face_editor_mouth_grim, -1, 1, -0.06, 0.06)
		expression[0, 20, 2] -= map_float(face_editor_mouth_grim, -1, 1, -0.03, 0.03)
	else:
		expression[0, 19, 1] -= map_float(face_editor_mouth_grim, -1, 1, -0.05, 0.05)
		expression[0, 19, 2] -= map_float(face_editor_mouth_grim, -1, 1, -0.02, 0.02)
		expression[0, 20, 2] -= map_float(face_editor_mouth_grim, -1, 1, -0.03, 0.03)
	return expression


def edit_mouth_position(expression : Expression) -> Expression:
	face_editor_mouth_position_horizontal = state_manager.get_item('face_editor_mouth_position_horizontal')
	face_editor_mouth_position_vertical = state_manager.get_item('face_editor_mouth_position_vertical')
	expression[0, 19, 0] += map_float(face_editor_mouth_position_horizontal, -1, 1, -0.05, 0.05)
	expression[0, 20, 0] += map_float(face_editor_mouth_position_horizontal, -1, 1, -0.04, 0.04)
	if face_editor_mouth_position_vertical > 0:
		expression[0, 19, 1] -= map_float(face_editor_mouth_position_vertical, -1, 1, -0.04, 0.04)
		expression[0, 20, 1] -= map_float(face_editor_mouth_position_vertical, -1, 1, -0.02, 0.02)
	else:
		expression[0, 19, 1] -= map_float(face_editor_mouth_position_vertical, -1, 1, -0.05, 0.05)
		expression[0, 20, 1] -= map_float(face_editor_mouth_position_vertical, -1, 1, -0.04, 0.04)
	return expression


def edit_mouth_pout(expression : Expression) -> Expression:
	face_editor_mouth_pout = state_manager.get_item('face_editor_mouth_pout')
	if face_editor_mouth_pout > 0:
		expression[0, 19, 1] -= map_float(face_editor_mouth_pout, -1, 1, -0.022, 0.022)
		expression[0, 19, 2] += map_float(face_editor_mouth_pout, -1, 1, -0.025, 0.025)
		expression[0, 20, 2] -= map_float(face_editor_mouth_pout, -1, 1, -0.002, 0.002)
	else:
		expression[0, 19, 1] += map_float(face_editor_mouth_pout, -1, 1, -0.022, 0.022)
		expression[0, 19, 2] += map_float(face_editor_mouth_pout, -1, 1, -0.025, 0.025)
		expression[0, 20, 2] -= map_float(face_editor_mouth_pout, -1, 1, -0.002, 0.002)
	return expression


def edit_mouth_purse(expression : Expression) -> Expression:
	face_editor_mouth_purse = state_manager.get_item('face_editor_mouth_purse')
	if face_editor_mouth_purse > 0:
		expression[0, 19, 1] -= map_float(face_editor_mouth_purse, -1, 1, -0.04, 0.04)
		expression[0, 19, 2] -= map_float(face_editor_mouth_purse, -1, 1, -0.02, 0.02)
	else:
		expression[0, 14, 1] -= map_float(face_editor_mouth_purse, -1, 1, -0.02, 0.02)
		expression[0, 17, 2] += map_float(face_editor_mouth_purse, -1, 1, -0.01, 0.01)
		expression[0, 19, 2] -= map_float(face_editor_mouth_purse, -1, 1, -0.015, 0.015)
		expression[0, 20, 2] -= map_float(face_editor_mouth_purse, -1, 1, -0.002, 0.002)
	return expression


def edit_mouth_smile(expression : Expression) -> Expression:
	face_editor_mouth_smile = state_manager.get_item('face_editor_mouth_smile')
	if face_editor_mouth_smile > 0:
		expression[0, 20, 1] -= map_float(face_editor_mouth_smile, -1, 1, -0.015, 0.015)
		expression[0, 14, 1] -= map_float(face_editor_mouth_smile, -1, 1, -0.025, 0.025)
		expression[0, 17, 1] += map_float(face_editor_mouth_smile, -1, 1, -0.01, 0.01)
		expression[0, 17, 2] += map_float(face_editor_mouth_smile, -1, 1, -0.004, 0.004)
		expression[0, 3, 1] -= map_float(face_editor_mouth_smile, -1, 1, -0.0045, 0.0045)
		expression[0, 7, 1] -= map_float(face_editor_mouth_smile, -1, 1, -0.0045, 0.0045)
	else:
		expression[0, 14, 1] -= map_float(face_editor_mouth_smile, -1, 1, -0.02, 0.02)
		expression[0, 17, 1] += map_float(face_editor_mouth_smile, -1, 1, -0.003, 0.003)
		expression[0, 19, 1] += map_float(face_editor_mouth_smile, -1, 1, -0.02, 0.02)
		expression[0, 19, 2] -= map_float(face_editor_mouth_smile, -1, 1, -0.005, 0.005)
		expression[0, 20, 2] += map_float(face_editor_mouth_smile, -1, 1, -0.01, 0.01)
		expression[0, 3, 1] += map_float(face_editor_mouth_smile, -1, 1, -0.0045, 0.0045)
		expression[0, 7, 1] += map_float(face_editor_mouth_smile, -1, 1, -0.0045, 0.0045)
	return expression


def calc_distance_ratio(face_landmark_68 : FaceLandmark68, top_index : int, bottom_index : int, left_index : int, right_index : int) -> float:
	vertical_direction = face_landmark_68[top_index] - face_landmark_68[bottom_index]
	horizontal_direction = face_landmark_68[left_index] - face_landmark_68[right_index]
	distance_ratio = float(numpy.linalg.norm(vertical_direction) / (numpy.linalg.norm(horizontal_direction) + 1e-6))
	return distance_ratio


def prepare_crop_frame(crop_vision_frame : VisionFrame) -> VisionFrame:
	crop_vision_frame = cv2.resize(crop_vision_frame, (256, 256), interpolation = cv2.INTER_AREA)
	crop_vision_frame = crop_vision_frame[:, :, ::-1] / 255.0
	crop_vision_frame = numpy.expand_dims(crop_vision_frame.transpose(2, 0, 1), axis = 0).astype(numpy.float32)
	return crop_vision_frame


def normalize_crop_frame(crop_vision_frame : VisionFrame) -> VisionFrame:
	crop_vision_frame = crop_vision_frame.transpose(1, 2, 0).clip(0, 1)
	crop_vision_frame = (crop_vision_frame * 255.0)
	crop_vision_frame = crop_vision_frame.astype(numpy.uint8)[:, :, ::-1]
	return crop_vision_frame


def get_reference_frame(source_face : Face, target_face : Face, temp_vision_frame : VisionFrame) -> VisionFrame:
	pass


def process_frame(inputs : FaceEditorInputs) -> VisionFrame:
	reference_faces = inputs.get('reference_faces')
	target_vision_frame = inputs.get('target_vision_frame')
	many_faces = sort_and_filter_faces(get_many_faces([ target_vision_frame ]))

	if state_manager.get_item('face_selector_mode') == 'many':
		if many_faces:
			for target_face in many_faces:
				target_vision_frame = edit_face(target_face, target_vision_frame)
	if state_manager.get_item('face_selector_mode') == 'one':
		target_face = get_one_face(many_faces)
		if target_face:
			target_vision_frame = edit_face(target_face, target_vision_frame)
	if state_manager.get_item('face_selector_mode') == 'reference':
		similar_faces = find_similar_faces(many_faces, reference_faces, state_manager.get_item('reference_face_distance'))
		if similar_faces:
			for similar_face in similar_faces:
				target_vision_frame = edit_face(similar_face, target_vision_frame)
	return target_vision_frame


def process_frames(source_path : List[str], queue_payloads : List[QueuePayload], update_progress : UpdateProgress) -> None:
	reference_faces = get_reference_faces() if 'reference' in state_manager.get_item('face_selector_mode') else None

	for queue_payload in process_manager.manage(queue_payloads):
		target_vision_path = queue_payload['frame_path']
		target_vision_frame = read_image(target_vision_path)
		output_vision_frame = process_frame(
		{
			'reference_faces': reference_faces,
			'target_vision_frame': target_vision_frame
		})
		write_image(target_vision_path, output_vision_frame)
		update_progress(1)


def process_image(source_path : str, target_path : str, output_path : str) -> None:
	reference_faces = get_reference_faces() if 'reference' in state_manager.get_item('face_selector_mode') else None
	target_vision_frame = read_static_image(target_path)
	output_vision_frame = process_frame(
	{
		'reference_faces': reference_faces,
		'target_vision_frame': target_vision_frame
	})
	write_image(output_path, output_vision_frame)


def process_video(source_paths : List[str], temp_frame_paths : List[str]) -> None:
	processors.multi_process_frames(None, temp_frame_paths, process_frames)
